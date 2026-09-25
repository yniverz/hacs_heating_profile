"""Climate control: drives an AC from a room sensor and the heating profile."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

import aiohttp
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    STATE_HOME,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AC,
    CONF_ACTIVE_FAN_MODE,
    CONF_AUTO_TUNE,
    CONF_AVERAGE_WINDOW,
    CONF_AWAY_AFTER,
    CONF_COMMAND_GRACE,
    CONF_COMPRESSOR,
    CONF_IDLE_FAN_MODE,
    CONF_IDLE_HVAC_MODE,
    CONF_LOOK_AHEAD,
    CONF_MAX_WAIT,
    CONF_OFFSET_MAX,
    CONF_OFFSET_STEP,
    CONF_OVERSHOOT,
    CONF_OVERSHOOT_WINDOW,
    CONF_PAUSE,
    CONF_PRESENCE,
    CONF_RAISE_IDLE,
    CONF_RAISE_MARGIN,
    CONF_ROOM_SENSOR,
    CONF_TREND_WINDOW,
    CONF_USE_FORECAST,
    CONTROL_DEFAULTS,
    CONTROL_INTERVAL_SECONDS,
    CONTROL_STORAGE_VERSION,
    DEFAULT_ACTIVE_FAN_MODE,
    DEFAULT_IDLE_FAN_MODE,
    DEFAULT_IDLE_HVAC_MODE,
    DEFAULT_OFFSET,
    DOMAIN,
    FORECAST_INTERVAL_MINUTES,
    FORECAST_MAX_AGE_HOURS,
    FORECAST_URL,
    HVAC_MODE_OFF,
    RESEND_INTERVAL_SECONDS,
    RUN_COOLING,
    RUN_HEATING,
    RUN_IDLE,
    SAVE_DELAY,
    STATE_AWAY,
    STATE_COOLING,
    STATE_DISABLED,
    STATE_HEATING,
    STATE_IDLE,
    STATE_OFF,
    STATE_PAUSED,
    STATE_UNAVAILABLE as CONTROL_UNAVAILABLE,
    STATE_WAITING,
)
from .control import (
    Decision,
    ForecastData,
    ForecastWindow,
    RoomHistory,
    Situation,
    decide,
    forecast_window,
    round_setpoint,
)
from .profile import HeatingProfileData

_LOGGER = logging.getLogger(__name__)

# Keep room readings long enough for every window that looks back.
_HISTORY_SECONDS = 4 * 3600


def control_storage_key(entry_id: str) -> str:
    """Return the storage key of the control state of an entry."""
    return f"{DOMAIN}.{entry_id}.control"


def control_configured(options: dict[str, Any]) -> bool:
    """Return True if the options set up a climate control."""
    return bool(options.get(CONF_ROOM_SENSOR) and options.get(CONF_AC))


def control_settings(options: dict[str, Any]) -> dict[str, Any]:
    """Options with defaults filled in."""
    settings: dict[str, Any] = dict(CONTROL_DEFAULTS)
    settings.update(
        {
            CONF_IDLE_HVAC_MODE: DEFAULT_IDLE_HVAC_MODE,
            CONF_IDLE_FAN_MODE: DEFAULT_IDLE_FAN_MODE,
            CONF_ACTIVE_FAN_MODE: DEFAULT_ACTIVE_FAN_MODE,
        }
    )
    settings.update({k: v for k, v in options.items() if v is not None})
    return settings


def _hm(ts: float) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%H:%M")


def _t(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f}"


@dataclass
class ControlState:
    """Persisted state of the control."""

    enabled: bool = True
    run: str = RUN_IDLE
    last_switch: float | None = None
    heat_ended: float | None = None
    cool_ended: float | None = None
    pause_until: float | None = None
    wait_since: float | None = None
    last_command: dict[str, Any] | None = None
    last_command_at: float | None = None
    offset_heat: float = DEFAULT_OFFSET
    offset_cool: float = DEFAULT_OFFSET
    offset_heat_changed: float | None = None
    offset_cool_changed: float | None = None
    last_cycle: dict[str, Any] | None = None  # {"kind", "stop", "ended"}
    reason: str = ""
    reason_since: float | None = None
    last_present: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlState:
        """Restore, ignoring unknown or broken values."""
        state = cls()
        for key, default in asdict(state).items():
            value = data.get(key, default)
            if value is None or isinstance(default, type(value)):
                setattr(state, key, value)
            elif isinstance(default, float) and isinstance(value, int):
                setattr(state, key, float(value))
            elif default is None and isinstance(value, (int, float, str, dict)):
                setattr(state, key, value)
        if state.run not in (RUN_IDLE, RUN_HEATING, RUN_COOLING):
            state.run = RUN_IDLE
        cmd = state.last_command
        if not (isinstance(cmd, dict) and {"mode", "temp", "fan"} <= cmd.keys()):
            state.last_command = None
        cycle = state.last_cycle
        if not (
            isinstance(cycle, dict)
            and cycle.get("kind") in ("heat", "cool")
            and isinstance(cycle.get("ended"), (int, float))
        ):
            state.last_cycle = None
        for key in ("offset_heat", "offset_cool"):
            if getattr(state, key) < 0:
                setattr(state, key, DEFAULT_OFFSET)
        return state


@dataclass
class ControlView:
    """What the entities show (result of the last evaluation)."""

    state: str = STATE_DISABLED
    status: str = "Control off"
    attributes: dict[str, Any] | None = None
    room: float | None = None
    trend: float | None = None
    forecast: ForecastWindow | None = None
    setpoint: float | None = None
    waiting_until: datetime | None = None
    paused_until: datetime | None = None


class ForecastSource:
    """Hourly Open-Meteo forecast for the Home Assistant location."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self._hass = hass
        self.data: ForecastData | None = None
        self.fetched_at: float | None = None
        self._failed_logged = False

    async def async_refresh(self) -> None:
        """Fetch the forecast; keeps the previous data on errors."""
        lat, lon = self._hass.config.latitude, self._hass.config.longitude
        params = {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "hourly": "temperature_2m,shortwave_radiation",
            "timeformat": "unixtime",
            "timezone": "UTC",
            "forecast_days": "2",
        }
        try:
            session = async_get_clientsession(self._hass)
            async with (
                asyncio.timeout(20),
                session.get(FORECAST_URL, params=params) as response,
            ):
                response.raise_for_status()
                payload = await response.json()
            hourly = payload["hourly"]
            data = ForecastData(
                times=[float(t) for t in hourly["time"]],
                temperatures=list(hourly["temperature_2m"]),
                radiation=list(hourly["shortwave_radiation"]),
            )
            if not data.times or not (
                len(data.times) == len(data.temperatures) == len(data.radiation)
            ):
                raise ValueError("inconsistent forecast")
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as err:
            if not self._failed_logged:
                _LOGGER.warning("Forecast from Open-Meteo unavailable: %s", err)
                self._failed_logged = True
            return
        self._failed_logged = False
        self.data = data
        self.fetched_at = dt_util.utcnow().timestamp()

    def window(self, now: float, minutes: float) -> ForecastWindow | None:
        """Forecast for the next minutes, or None if missing or stale."""
        if (
            self.fetched_at is None
            or now - self.fetched_at > FORECAST_MAX_AGE_HOURS * 3600
        ):
            return None
        return forecast_window(self.data, now, minutes)


class ClimateController:
    """Decides every minute whether the AC heats, cools or idles."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        profile: HeatingProfileData,
        options: dict[str, Any],
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.profile = profile
        self.settings = control_settings(options)
        self.room_sensor: str = options[CONF_ROOM_SENSOR]
        self.ac: str = options[CONF_AC]
        self.compressor: str | None = options.get(CONF_COMPRESSOR) or None
        self.presence: str | None = options.get(CONF_PRESENCE) or None
        self._store: Store[dict[str, Any]] = Store(
            hass, CONTROL_STORAGE_VERSION, control_storage_key(entry_id)
        )
        self.state = ControlState()
        self.view = ControlView()
        self.history = RoomHistory(_HISTORY_SECONDS)
        self.forecast = (
            ForecastSource(hass) if self.settings[CONF_USE_FORECAST] else None
        )
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._lock = asyncio.Lock()
        self._pending = False
        self._stopped = False
        self._warned_mode: str | None = None

    # ----- lifecycle -------------------------------------------------------

    async def async_start(self) -> None:
        """Load the state and start listening."""
        stored = await self._store.async_load() or {}
        self.state = ControlState.from_dict(stored)
        if self.state.enabled:
            self.view = ControlView(state=CONTROL_UNAVAILABLE, status="Starting")
        self._record_room(self.hass.states.get(self.room_sensor))
        hass = self.hass
        self._unsubs += [
            async_track_state_change_event(
                hass, [self.room_sensor], self._async_room_changed
            ),
            async_track_state_change_event(hass, [self.ac], self._async_ac_changed),
            async_track_time_interval(
                hass,
                self._async_tick,
                timedelta(seconds=CONTROL_INTERVAL_SECONDS),
                cancel_on_shutdown=True,
            ),
            self.profile.async_add_listener(self._schedule_evaluate),
        ]
        if self.presence:
            self._unsubs.append(
                async_track_state_change_event(
                    hass, [self.presence], self._async_trigger_evaluate
                )
            )
        if self.forecast is not None:
            self._unsubs.append(
                async_track_time_interval(
                    hass,
                    self._async_refresh_forecast,
                    timedelta(minutes=FORECAST_INTERVAL_MINUTES),
                    cancel_on_shutdown=True,
                )
            )
            # The first evaluation waits for the forecast, so a restart doesn't
            # start a run that the forecast would have postponed.
            hass.async_create_task(self._async_first_forecast(), f"{DOMAIN} forecast")
        else:
            self._schedule_evaluate()

    async def _async_first_forecast(self) -> None:
        assert self.forecast is not None
        await self.forecast.async_refresh()
        self._schedule_evaluate()

    async def async_stop(self) -> None:
        """Stop listening and save."""
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(asdict(self.state))

    async def async_remove_storage(self) -> None:
        """Delete the stored state."""
        await self._store.async_remove()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: asdict(self.state), SAVE_DELAY)

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> Callable[[], None]:
        """Register an entity update listener."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ----- inputs ----------------------------------------------------------

    @staticmethod
    def _number(state: State | None) -> float | None:
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _record_room(self, state: State | None) -> None:
        value = self._number(state)
        if value is not None:
            self.history.add(dt_util.utcnow().timestamp(), value)

    @callback
    def _async_room_changed(self, event: Event[EventStateChangedData]) -> None:
        self._record_room(event.data["new_state"])

    async def _async_tick(self, _now: datetime) -> None:
        await self.async_evaluate()

    async def _async_refresh_forecast(self, _now: datetime) -> None:
        if self.forecast is not None:
            await self.forecast.async_refresh()

    @callback
    def _async_trigger_evaluate(self, _event: Event[EventStateChangedData]) -> None:
        self._schedule_evaluate()

    @callback
    def _schedule_evaluate(self) -> None:
        if self._stopped:
            return
        self.hass.async_create_task(self.async_evaluate(), eager_start=True)

    def _present(self) -> bool:
        """Presence entity says present (unavailable counts as present)."""
        if not self.presence:
            return True
        state = self.hass.states.get(self.presence)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return True
        return state.state in (STATE_ON, STATE_HOME)

    # ----- user actions ----------------------------------------------------

    async def async_set_enabled(self, enabled: bool) -> None:
        """Switch the control on/off; switching on ends a pause."""
        self.state.enabled = enabled
        if enabled:
            self.state.pause_until = None
        self._save()
        if enabled:
            await self.async_evaluate()
        else:
            self._update_view_disabled()
            self._notify()

    async def async_end_pause(self) -> None:
        """End a pause after a manual change right away."""
        self.state.pause_until = None
        self._save()
        await self.async_evaluate()

    async def async_set_offset(self, kind: str, value: float) -> None:
        """Set the heating or cooling offset by hand."""
        now = dt_util.utcnow().timestamp()
        if kind == "heat":
            self.state.offset_heat, self.state.offset_heat_changed = value, now
        else:
            self.state.offset_cool, self.state.offset_cool_changed = value, now
        self._save()
        await self.async_evaluate()

    # ----- manual changes on the AC ---------------------------------------

    def _ac_tuple(self, mode: str, temp: Any, fan: Any) -> dict[str, Any]:
        """Normalize a command/AC state to the parts the control sets."""
        idle_mode = self.settings[CONF_IDLE_HVAC_MODE]
        try:
            temp_value = round(float(temp), 1) if temp is not None else None
        except (TypeError, ValueError):
            temp_value = None
        return {
            "mode": mode,
            "temp": temp_value if mode not in (HVAC_MODE_OFF, idle_mode) else None,
            "fan": fan if mode != HVAC_MODE_OFF else None,
        }

    @callback
    def _async_ac_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if not self.state.enabled or self.state.last_command is None:
            return
        bad = (STATE_UNAVAILABLE, STATE_UNKNOWN)
        if old is None or new is None or old.state in bad or new.state in bad:
            return
        keys = (ATTR_TEMPERATURE, ATTR_FAN_MODE)
        if old.state == new.state and all(
            old.attributes.get(k) == new.attributes.get(k) for k in keys
        ):
            return  # e.g. only the current temperature changed
        now = dt_util.utcnow().timestamp()
        if (
            self.state.last_command_at is not None
            and now - self.state.last_command_at <= self.settings[CONF_COMMAND_GRACE]
        ):
            return
        last = self.state.last_command
        current = self._ac_tuple(
            new.state,
            new.attributes.get(ATTR_TEMPERATURE),
            new.attributes.get(ATTR_FAN_MODE),
        )
        # Compare only what the control actually set.
        if all(last.get(k) is None or last.get(k) == current[k] for k in current) and (
            last["mode"] == current["mode"]
        ):
            return
        self.state.pause_until = now + self.settings[CONF_PAUSE] * 60
        _LOGGER.info(
            "Manual change on %s (%s -> %s), pausing the control until %s",
            self.ac,
            last,
            current,
            _hm(self.state.pause_until),
        )
        self._save()
        self._schedule_evaluate()

    # ----- evaluation ------------------------------------------------------

    async def async_evaluate(self) -> None:
        """Evaluate now (skipped if an evaluation is already running)."""
        if self._stopped:
            return
        if self._lock.locked():
            self._pending = True
            return
        async with self._lock:
            while True:
                self._pending = False
                try:
                    await self._async_evaluate()
                except Exception:
                    _LOGGER.exception("Climate control evaluation failed")
                if not self._pending or self._stopped:
                    break
        self._notify()

    def _update_view_disabled(self) -> None:
        self.view = ControlView(state=STATE_DISABLED, status="Control off")

    async def _async_evaluate(self) -> None:
        st = self.state
        s = self.settings
        now = dt_util.utcnow().timestamp()

        if not st.enabled:
            self._update_view_disabled()
            return

        # Presence bookkeeping (also while paused, so away time keeps counting).
        if self._present() or st.last_present is None:
            st.last_present = now
        away = bool(self.presence) and now - st.last_present >= s[CONF_AWAY_AFTER] * 60

        ac_state = self.hass.states.get(self.ac)
        room_state = self.hass.states.get(self.room_sensor)
        self._record_room(room_state)
        room_now = self._number(room_state)
        room = (
            self.history.mean(now, s[CONF_AVERAGE_WINDOW] * 60)
            if room_now is not None
            else None
        )
        trend = (
            self.history.change(now, s[CONF_TREND_WINDOW] * 60)
            * 30
            / max(s[CONF_TREND_WINDOW], 1)
        )
        fc = (
            self.forecast.window(now, s[CONF_MAX_WAIT])
            if self.forecast is not None
            else None
        )

        if ac_state is None or ac_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self.view = ControlView(
                state=CONTROL_UNAVAILABLE,
                status="AC unavailable",
                room=room,
                trend=trend,
                forecast=fc,
            )
            self._save()
            return

        if st.pause_until is not None and now < st.pause_until:
            self.view = ControlView(
                state=STATE_PAUSED,
                status=f"Paused until {_hm(st.pause_until)} (manual change on the AC)",
                room=room,
                trend=trend,
                forecast=fc,
                paused_until=dt_util.utc_from_timestamp(st.pause_until),
            )
            self._save()
            return
        if st.pause_until is not None:
            st.pause_until = None

        # Within the look-ahead before a day/night switch, act as if the next
        # period had already started (saves heating/cooling the old range
        # needs, pre-heats/-cools for the new one).
        now_dt = dt_util.utcnow()
        period = self.profile.period(now_dt)
        period_low, period_high = self.profile.period_range(period)
        ahead: str | None = None
        look = s[CONF_LOOK_AHEAD] * 60
        if look > 0:
            switch_at, next_period = self.profile.upcoming(now_dt)
            if next_period != period and (switch_at - now_dt).total_seconds() <= look:
                period_low, period_high = self.profile.period_range(next_period)
                ahead = f"{next_period} from {_hm(switch_at.timestamp())}"
        situation = Situation(
            now=now,
            run=st.run,
            profile_mode=self.profile.hvac_mode,
            period_low=period_low,
            period_high=period_high,
            room=room,
            trend=trend,
            forecast=fc,
            last_switch=st.last_switch,
            heat_ended=st.heat_ended,
            cool_ended=st.cool_ended,
            wait_since=st.wait_since,
        )
        d = decide(situation, s)

        self._check_overshoot(now, d)
        self._maybe_raise_offset(now, room, d)

        # Record switches.
        if d.run != st.run:
            if st.run == RUN_HEATING:
                st.heat_ended = now
                st.last_cycle = {"kind": "heat", "stop": d.stop_heat, "ended": now}
            elif st.run == RUN_COOLING:
                st.cool_ended = now
                st.last_cycle = {"kind": "cool", "stop": d.stop_cool, "ended": now}
            if d.run in (RUN_HEATING, RUN_COOLING):
                st.reason = self._reason(d, room, trend, ahead)
                st.reason_since = now
            else:
                st.reason, st.reason_since = "", None
            _LOGGER.debug("Run %s -> %s (%s)", st.run, d.run, st.reason)
            st.run = d.run
            st.last_switch = now
        st.wait_since = d.wait_since

        # What the AC should do.
        idle_mode = s[CONF_IDLE_HVAC_MODE]
        attrs = ac_state.attributes
        setpoint: float | None = None
        if self.profile.hvac_mode == HVAC_MODE_OFF:
            want = self._ac_tuple(HVAC_MODE_OFF, None, None)
        elif d.run == RUN_HEATING and d.stop_heat is not None:
            setpoint = self._setpoint(d.stop_heat + st.offset_heat, attrs)
            want = self._ac_tuple("heat", setpoint, s[CONF_ACTIVE_FAN_MODE])
        elif d.run == RUN_COOLING and d.stop_cool is not None:
            setpoint = self._setpoint(d.stop_cool - st.offset_cool, attrs)
            want = self._ac_tuple("cool", setpoint, s[CONF_ACTIVE_FAN_MODE])
        elif away:
            want = self._ac_tuple(HVAC_MODE_OFF, None, None)
        else:
            want = self._ac_tuple(idle_mode, None, s[CONF_IDLE_FAN_MODE])
        await self._async_send(want, ac_state)

        self.view = self._build_view(d, room, trend, fc, setpoint, away, ahead)
        self._save()

    def _setpoint(self, value: float, attrs: dict[str, Any]) -> float:
        def num(key: str, default: float) -> float:
            try:
                return float(attrs.get(key, default))
            except (TypeError, ValueError):
                return default

        return round_setpoint(
            value,
            num(ATTR_TARGET_TEMP_STEP, 0.5),
            num(ATTR_MIN_TEMP, 16.0),
            num(ATTR_MAX_TEMP, 30.0),
        )

    async def _async_send(self, want: dict[str, Any], ac_state: State) -> None:
        """Send only what differs from the AC's current state."""
        attrs = ac_state.attributes
        hvac_modes = attrs.get(ATTR_HVAC_MODES) or []
        fan_modes = attrs.get(ATTR_FAN_MODES) or []
        if hvac_modes and want["mode"] not in hvac_modes:
            if self._warned_mode != want["mode"]:
                _LOGGER.warning("%s does not support mode %s", self.ac, want["mode"])
                self._warned_mode = want["mode"]
            return
        self._warned_mode = None
        if want["fan"] is not None and want["fan"] not in fan_modes:
            want = {**want, "fan": None}  # the AC has no such fan mode
        current = self._ac_tuple(
            ac_state.state, attrs.get(ATTR_TEMPERATURE), attrs.get(ATTR_FAN_MODE)
        )
        send_mode = current["mode"] != want["mode"]
        send_temp = want["temp"] is not None and (
            send_mode or current["temp"] != want["temp"]
        )
        send_fan = want["fan"] is not None and (
            send_mode or current["fan"] != want["fan"]
        )
        if not (send_mode or send_temp or send_fan):
            return
        now = dt_util.utcnow().timestamp()
        if (
            want == self.state.last_command
            and self.state.last_command_at is not None
            and now - self.state.last_command_at < RESEND_INTERVAL_SECONDS
        ):
            return  # sent recently; the AC hasn't followed (yet)
        # Remember first, so the manual-change detection ignores the echo.
        self.state.last_command = want
        self.state.last_command_at = now
        calls: list[tuple[str, dict[str, Any]]] = []
        if send_mode:
            calls.append((SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: want["mode"]}))
        if send_temp:
            calls.append((SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: want["temp"]}))
        if send_fan:
            calls.append((SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: want["fan"]}))
        for service, data in calls:
            try:
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    service,
                    {ATTR_ENTITY_ID: self.ac, **data},
                    blocking=True,
                )
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("Could not %s on %s: %s", service, self.ac, err)

    # ----- offset tuning ---------------------------------------------------

    def _check_overshoot(self, now: float, d: Decision) -> None:
        """After a run: overshoot -> lower the offset (once per run)."""
        cycle = self.state.last_cycle
        if cycle is None:
            return
        window_end = cycle["ended"] + self.settings[CONF_OVERSHOOT_WINDOW] * 60
        starting = d.run != RUN_IDLE and self.state.run == RUN_IDLE
        if now < window_end and not starting:
            return
        self.state.last_cycle = None
        stop = cycle.get("stop")
        if not self.settings[CONF_AUTO_TUNE] or stop is None:
            return
        until = min(now, window_end)
        heat = cycle["kind"] == "heat"
        extreme = self.history.extreme_since(cycle["ended"], until, highest=heat)
        if extreme is None:
            return
        overshoot = extreme - stop if heat else stop - extreme
        if overshoot > self.settings[CONF_OVERSHOOT]:
            self._change_offset("heat" if heat else "cool", -1, now, overshoot)

    def _maybe_raise_offset(self, now: float, room: float | None, d: Decision) -> None:
        """During a run: compressor idle while short of the stop point -> raise."""
        st, s = self.state, self.settings
        if not s[CONF_AUTO_TUNE] or not self.compressor or room is None:
            return
        if st.run not in (RUN_HEATING, RUN_COOLING) or d.run != st.run:
            return
        idle = s[CONF_RAISE_IDLE] * 60
        if st.last_switch is None or now - st.last_switch < idle:
            return
        comp = self.hass.states.get(self.compressor)
        if comp is None or comp.state != "off":
            return
        if now - comp.last_changed.timestamp() < idle:
            return
        heat = st.run == RUN_HEATING
        stop = d.stop_heat if heat else d.stop_cool
        if stop is None:
            return
        margin = s[CONF_RAISE_MARGIN]
        if (heat and room >= stop - margin) or (not heat and room <= stop + margin):
            return
        changed = st.offset_heat_changed if heat else st.offset_cool_changed
        if changed is not None and now - changed < idle:
            return
        self._change_offset("heat" if heat else "cool", +1, now, None)

    def _change_offset(
        self, kind: str, direction: int, now: float, overshoot: float | None
    ) -> None:
        s = self.settings
        step, top = s[CONF_OFFSET_STEP], s[CONF_OFFSET_MAX]
        old = self.state.offset_heat if kind == "heat" else self.state.offset_cool
        new = round(min(max(old + direction * step, 0.0), top), 2)
        if new == old:
            return
        if kind == "heat":
            self.state.offset_heat, self.state.offset_heat_changed = new, now
        else:
            self.state.offset_cool, self.state.offset_cool_changed = new, now
        if overshoot is None:
            _LOGGER.info(
                "Compressor idle, room short of target: %s offset %s", kind, new
            )
        else:
            _LOGGER.info("Overshoot %.1f °C: %s offset %s", overshoot, kind, new)

    # ----- texts -----------------------------------------------------------

    def _reason(
        self, d: Decision, room: float | None, trend: float, ahead: str | None
    ) -> str:
        text = self._reason_text(d, room, trend)
        return f"{text} ({ahead})" if ahead else text

    def _reason_text(self, d: Decision, room: float | None, trend: float) -> str:
        s = self.settings
        r = _t(room)
        waited = d.extra.get("waited_before_start", 0.0)
        if d.run == RUN_HEATING:
            if d.hard:
                return (
                    f"Hard limit: room {r} °C, more than {s['hard_margin']} °C "
                    f"below the minimum {_t(d.low)} °C"
                )
            base = f"Too cold ({r} °C, minimum {_t(d.low)} °C)"
            if not d.warmth_forecast:
                return f"{base}, no sun or warmth expected"
            if trend <= -s["fast_trend"]:
                return f"{base} and cooling down quickly ({trend:+.2f} °C/30 min)"
            if waited >= s["max_wait"]:
                return f"{base}, waited {s['max_wait']:g} min for sun or warmth"
            return base
        if d.hard:
            return (
                f"Hard limit: room {r} °C, more than {s['hard_margin']} °C "
                f"above the maximum {_t(d.high)} °C"
            )
        base = f"Too warm ({r} °C, maximum {_t(d.high)} °C)"
        if not d.cool_forecast:
            return f"{base}, no cooler outside air expected"
        if trend >= s["fast_trend"]:
            return f"{base} and warming up quickly ({trend:+.2f} °C/30 min)"
        if waited >= s["max_wait"]:
            return f"{base}, waited {s['max_wait']:g} min for cooler outside air"
        return base

    def _build_view(
        self,
        d: Decision,
        room: float | None,
        trend: float,
        fc: ForecastWindow | None,
        setpoint: float | None,
        away: bool,
        ahead: str | None = None,
    ) -> ControlView:
        st, s = self.state, self.settings
        note = f" ({ahead})" if ahead else ""
        idle_label = s[CONF_IDLE_HVAC_MODE].replace("_", " ")
        idle_text = "AC off" if away else idle_label
        waiting_until: datetime | None = None

        if self.profile.hvac_mode == HVAC_MODE_OFF:
            state, status = STATE_OFF, "Profile off – AC off"
        elif room is None:
            state = CONTROL_UNAVAILABLE
            status = f"Room sensor unavailable – {idle_text}"
        elif d.run == RUN_HEATING:
            state = STATE_HEATING
            status = (
                f"Heating – {_t(d.stop_heat)} °C reached, minimum run until "
                f"{_hm(d.min_run_until)}"
                if d.min_run_until is not None
                else f"Heating to {_t(d.stop_heat)} °C"
            ) + note
        elif d.run == RUN_COOLING:
            state = STATE_COOLING
            status = (
                f"Cooling – {_t(d.stop_cool)} °C reached, minimum run until "
                f"{_hm(d.min_run_until)}"
                if d.min_run_until is not None
                else f"Cooling to {_t(d.stop_cool)} °C"
            ) + note
        elif d.waiting:
            state = STATE_WAITING
            word = "Too cold" if d.heat_zone else "Too warm"
            if d.min_pause_until is not None:
                status = f"{word} – minimum pause until {_hm(d.min_pause_until)}"
            elif d.lockout_until is not None:
                other = "cooling" if d.heat_zone else "heating"
                status = f"{word} – locked after {other} until {_hm(d.lockout_until)}"
            elif (d.heat_zone and d.warmth_coming) or (d.cool_zone and d.free_cooling):
                assert d.wait_since is not None
                end = d.wait_since + s[CONF_MAX_WAIT] * 60
                waiting_until = dt_util.utc_from_timestamp(end)
                what = "sun or warmth" if d.heat_zone else "cooler outside air"
                status = f"{word} – waiting for {what} until {_hm(end)}"
            else:
                status = word
            status += note
            if away:
                status += " (away – AC off)"
        elif away:
            state = STATE_AWAY
            assert st.last_present is not None
            status = f"Away since {_hm(st.last_present)} – AC off"
        else:
            state = STATE_IDLE
            if d.low is not None and d.high is not None:
                rng = f"{_t(d.low)}–{_t(d.high)} °C"
            elif d.low is not None:
                rng = f"≥ {_t(d.low)} °C"
            else:
                rng = f"≤ {_t(d.high)} °C"
            status = f"In range {rng}{note} – {idle_label}"

        attributes = {
            "run": d.run,
            "room": None if room is None else round(room, 2),
            "minimum": d.low,
            "maximum": d.high,
            "stop": d.stop_heat
            if d.run == RUN_HEATING
            else d.stop_cool
            if d.run == RUN_COOLING
            else None,
            "ac_setpoint": setpoint,
            "trend_per_30min": round(trend, 2),
            "waited_min": round(d.waited),
            "warmth_coming": d.warmth_coming,
            "free_cooling": d.free_cooling,
            "away": away,
            "look_ahead": ahead,
            "offset_heat": st.offset_heat,
            "offset_cool": st.offset_cool,
        }
        return ControlView(
            state=state,
            status=status,
            attributes=attributes,
            room=room,
            trend=trend,
            forecast=fc,
            setpoint=setpoint,
            waiting_until=waiting_until,
        )
