"""Climate control: picks heat/cool/neutral and sets the AC's setpoint.

Layer 1 (control.decide) picks the mode and changes it rarely. Layer 2 sets
the AC's setpoint to the target plus a learned offset and lets the AC
modulate by itself.
"""

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
    CONF_ACTIVE_POWER,
    CONF_AUTO_TUNE,
    CONF_AVERAGE_WINDOW,
    CONF_COMMAND_GRACE,
    CONF_COMPRESSOR,
    CONF_COOL_MARGIN,
    CONF_DRIFT_TIME,
    CONF_EXIT_WINDOW,
    CONF_HARD_MARGIN,
    CONF_HIGH_POWER,
    CONF_IDLE_FAN_MODE,
    CONF_IDLE_HVAC_MODE,
    CONF_LEARN_AVERAGE,
    CONF_LEARN_INTERVAL,
    CONF_LEARN_SETTLE,
    CONF_LOOK_AHEAD,
    CONF_MAX_WAIT,
    CONF_PAUSE,
    CONF_POWER_SENSOR,
    CONF_ROOM_SENSOR,
    CONF_TREND_WINDOW,
    CONF_USE_FORECAST,
    CONF_WARMTH_MARGIN,
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
    MODE_COOL,
    MODE_HEAT,
    MODE_NEUTRAL,
    MODES,
    RESEND_INTERVAL_SECONDS,
    SAVE_DELAY,
    STATE_COOLING,
    STATE_DISABLED,
    STATE_HEATING,
    STATE_NEUTRAL,
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
    learn_step,
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
    mode: str = MODE_NEUTRAL
    mode_since: float | None = None
    heat_ended: float | None = None
    cool_ended: float | None = None
    pause_until: float | None = None
    wait_since: float | None = None
    drift_until: float | None = None
    drift_side: str | None = None  # "heat" / "cool": which limit may drift
    idle_since: float | None = None
    last_command: dict[str, Any] | None = None
    last_command_at: float | None = None
    offset_heat: float = DEFAULT_OFFSET
    offset_cool: float = DEFAULT_OFFSET
    learned_at: float | None = None
    last_target: float | None = None
    target_changed_at: float | None = None
    reason: str = ""
    reason_since: float | None = None

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
        if state.mode not in MODES:
            state.mode = MODE_NEUTRAL
        if state.drift_side not in (None, "heat", "cool"):
            state.drift_side = None
        cmd = state.last_command
        if not (isinstance(cmd, dict) and {"mode", "temp", "fan"} <= cmd.keys()):
            state.last_command = None
        for key in (
            "mode_since",
            "heat_ended",
            "cool_ended",
            "pause_until",
            "wait_since",
            "drift_until",
            "idle_since",
            "last_command_at",
            "learned_at",
            "last_target",
            "target_changed_at",
            "reason_since",
        ):
            if not isinstance(getattr(state, key), (int, float, type(None))):
                setattr(state, key, None)
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
    """Decides every minute: mode (layer 1) and AC setpoint (layer 2)."""

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
        self.power_sensor: str | None = options.get(CONF_POWER_SENSOR) or None
        self.compressor: str | None = options.get(CONF_COMPRESSOR) or None
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
            # start a mode that the forecast would have postponed.
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
    def _schedule_evaluate(self) -> None:
        if self._stopped:
            return
        self.hass.async_create_task(self.async_evaluate(), eager_start=True)

    def _activity(self) -> tuple[bool | None, bool | None, float | None]:
        """(working, working hard, power) of the AC; None if unknown."""
        if self.power_sensor:
            power = self._number(self.hass.states.get(self.power_sensor))
            if power is not None:
                s = self.settings
                return power >= s[CONF_ACTIVE_POWER], power >= s[CONF_HIGH_POWER], power
        if self.compressor:
            comp = self.hass.states.get(self.compressor)
            if comp is not None and comp.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                return comp.state == STATE_ON, None, None
        return None, None, None

    # ----- user actions ----------------------------------------------------

    async def async_set_enabled(self, enabled: bool) -> None:
        """Switch the control on/off; switching on ends a pause."""
        self.state.enabled = enabled
        if enabled:
            self.state.pause_until = None
        self._save()
        await self.async_evaluate()

    async def async_end_pause(self) -> None:
        """End a pause after a manual change right away."""
        self.state.pause_until = None
        self._save()
        await self.async_evaluate()

    async def async_set_offset(self, kind: str, value: float) -> None:
        """Set the heating or cooling offset by hand."""
        if kind == "heat":
            self.state.offset_heat = value
        else:
            self.state.offset_cool = value
        self.state.learned_at = dt_util.utcnow().timestamp()
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
        if last["mode"] == current["mode"] and all(
            last.get(k) is None or last.get(k) == current[k] for k in current
        ):
            return
        if self.settings[CONF_PAUSE] <= 0:
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
        """Evaluate now (queued once if an evaluation is already running)."""
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

    def _effective_range(self, now_dt: datetime) -> tuple[float, float, str | None]:
        """Range of the current period, or of the next one within look-ahead."""
        period = self.profile.period(now_dt)
        low, high = self.profile.period_range(period)
        look = self.settings[CONF_LOOK_AHEAD] * 60
        if look > 0:
            switch_at, next_period = self.profile.upcoming(now_dt)
            if next_period != period and (switch_at - now_dt).total_seconds() <= look:
                low, high = self.profile.period_range(next_period)
                return low, high, f"{next_period} from {_hm(switch_at.timestamp())}"
        return low, high, None

    async def _async_evaluate(self) -> None:
        st, s = self.state, self.settings
        now_dt = dt_util.utcnow()
        now = now_dt.timestamp()

        ac_state = self.hass.states.get(self.ac)
        room_state = self.hass.states.get(self.room_sensor)
        self._record_room(room_state)
        room_ok = self._number(room_state) is not None
        room = self.history.mean(now, s[CONF_AVERAGE_WINDOW] * 60) if room_ok else None
        trend = (
            self.history.change(now, s[CONF_TREND_WINDOW] * 60)
            * 30
            / max(s[CONF_TREND_WINDOW], 1)
        )
        wait_fc = exit_fc = None
        if self.forecast is not None:
            wait_fc = self.forecast.window(now, s[CONF_MAX_WAIT])
            exit_fc = self.forecast.window(now, s[CONF_EXIT_WINDOW])

        if not st.enabled:
            # Nothing is sent, but the measurements keep updating.
            self.view = ControlView(
                state=STATE_DISABLED,
                status="Control off",
                attributes={
                    "room": None if room is None else round(room, 2),
                    "trend_per_30min": round(trend, 2),
                },
                room=room,
                trend=trend,
                forecast=wait_fc,
            )
            return

        if ac_state is None or ac_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self.view = ControlView(
                state=CONTROL_UNAVAILABLE,
                status="AC unavailable",
                room=room,
                trend=trend,
                forecast=wait_fc,
            )
            self._save()
            return

        if st.pause_until is not None and now < st.pause_until:
            self.view = ControlView(
                state=STATE_PAUSED,
                status=f"Paused until {_hm(st.pause_until)} (manual change on the AC)",
                room=room,
                trend=trend,
                forecast=wait_fc,
                paused_until=dt_util.utc_from_timestamp(st.pause_until),
            )
            self._save()
            return
        st.pause_until = None

        active, high_power, power = self._activity()
        if st.mode == MODE_NEUTRAL or active is None or active:
            st.idle_since = None
        elif st.idle_since is None:
            st.idle_since = now

        low, high, ahead = self._effective_range(now_dt)
        situation = Situation(
            now=now,
            mode=st.mode,
            profile_mode=self.profile.hvac_mode,
            period_low=low,
            period_high=high,
            room=room,
            trend=trend,
            wait_forecast=wait_fc,
            exit_forecast=exit_fc,
            idle_since=st.idle_since,
            mode_since=st.mode_since,
            heat_ended=st.heat_ended,
            cool_ended=st.cool_ended,
            wait_since=st.wait_since,
            drift_until=st.drift_until,
            drift_side=st.drift_side,
        )
        d = decide(situation, s)

        if d.mode != st.mode:
            self._switch_mode(d, room, trend, ahead, now)
        st.wait_since = d.wait_since if d.mode == MODE_NEUTRAL else None
        if d.mode == MODE_NEUTRAL and d.drift_until is None:
            st.drift_until = st.drift_side = None

        # Layer 2: target and setpoint.
        target = (
            d.target_heat
            if d.mode == MODE_HEAT
            else d.target_cool
            if d.mode == MODE_COOL
            else None
        )
        if target != st.last_target:
            st.last_target, st.target_changed_at = target, now
        if target is not None:
            self._learn(
                d.mode == MODE_HEAT,
                target,
                self._number(room_state),
                room,
                active,
                high_power,
                now,
            )

        attrs = ac_state.attributes
        setpoint: float | None = None
        if self.profile.hvac_mode == HVAC_MODE_OFF:
            want = self._ac_tuple(HVAC_MODE_OFF, None, None)
        elif d.mode == MODE_HEAT and target is not None:
            setpoint = self._setpoint(target + st.offset_heat, attrs)
            want = self._ac_tuple("heat", setpoint, s[CONF_ACTIVE_FAN_MODE])
        elif d.mode == MODE_COOL and target is not None:
            setpoint = self._setpoint(target - st.offset_cool, attrs)
            want = self._ac_tuple("cool", setpoint, s[CONF_ACTIVE_FAN_MODE])
        else:
            want = self._ac_tuple(s[CONF_IDLE_HVAC_MODE], None, s[CONF_IDLE_FAN_MODE])
        await self._async_send(want, ac_state)

        self.view = self._build_view(
            d, room, trend, wait_fc, target, setpoint, ahead, active, power, (low, high)
        )
        self._save()

    def _switch_mode(
        self,
        d: Decision,
        room: float | None,
        trend: float,
        ahead: str | None,
        now: float,
    ) -> None:
        st = self.state
        if st.mode == MODE_HEAT:
            st.heat_ended = now
        elif st.mode == MODE_COOL:
            st.cool_ended = now
        if d.mode in (MODE_HEAT, MODE_COOL):
            st.reason = self._reason(d, room, trend, ahead)
            st.reason_since = now
        else:
            st.reason, st.reason_since = "", None
        if d.early_exit:
            st.drift_until, st.drift_side = d.drift_until, d.drift_side
        else:
            st.drift_until = st.drift_side = None
        _LOGGER.debug("Mode %s -> %s (%s)", st.mode, d.mode, st.reason)
        st.mode = d.mode
        st.mode_since = now
        st.idle_since = None

    def _learn(
        self,
        heating: bool,
        target: float,
        room_now: float | None,
        room: float | None,
        active: bool | None,
        high_power: bool | None,
        now: float,
    ) -> None:
        """One learning step of the offset, if it's time."""
        st, s = self.state, self.settings
        if not s[CONF_AUTO_TUNE]:
            return
        settle = s[CONF_LEARN_SETTLE] * 60
        if st.mode_since is not None and now - st.mode_since < settle:
            return
        if st.target_changed_at is not None and now - st.target_changed_at < settle:
            return
        if (
            st.learned_at is not None
            and now - st.learned_at < s[CONF_LEARN_INTERVAL] * 60
        ):
            return
        offset = st.offset_heat if heating else st.offset_cool
        new = learn_step(
            heating=heating,
            target=target,
            room_now=room_now,
            room=room,
            room_long=self.history.mean(now, s[CONF_LEARN_AVERAGE] * 60),
            offset=offset,
            active=active,
            high_power=high_power,
            settings=s,
        )
        if new is None:
            return
        if heating:
            st.offset_heat = new
        else:
            st.offset_cool = new
        st.learned_at = now
        _LOGGER.info(
            "Learned %s offset %.2f -> %.2f",
            "heating" if heating else "cooling",
            offset,
            new,
        )

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

    # ----- texts -----------------------------------------------------------

    def _reason(
        self, d: Decision, room: float | None, trend: float, ahead: str | None
    ) -> str:
        text = self._reason_text(d, room, trend)
        return f"{text} ({ahead})" if ahead else text

    def _reason_text(self, d: Decision, room: float | None, trend: float) -> str:
        s = self.settings
        r = _t(room)
        heat = d.mode == MODE_HEAT
        limit = d.low if heat else d.high
        if d.reason == "hard":
            side = "below the minimum" if heat else "above the maximum"
            return (
                f"Hard limit: room {r} °C, more than {s[CONF_HARD_MARGIN]:g} °C "
                f"{side} {_t(limit)} °C"
            )
        base = (
            f"Near minimum ({r} °C, minimum {_t(limit)} °C)"
            if heat
            else f"Near maximum ({r} °C, maximum {_t(limit)} °C)"
        )
        free = "sun or warmth" if heat else "cooler outside air"
        if d.reason == "no_forecast":
            return (
                f"{base}, no sun or warmth expected"
                if heat
                else f"{base}, no cooler outside air expected"
            )
        if d.reason == "fast":
            how = "cooling down" if heat else "warming up"
            return f"{base} and {how} quickly ({trend:+.2f} °C/30 min)"
        if d.reason == "waited":
            return f"{base}, waited {s[CONF_MAX_WAIT]:g} min for {free}"
        if d.reason == "drift_over":
            return f"{base}, {free} didn't come within {s[CONF_DRIFT_TIME]:g} min"
        return base

    def _build_view(
        self,
        d: Decision,
        room: float | None,
        trend: float,
        fc: ForecastWindow | None,
        target: float | None,
        setpoint: float | None,
        ahead: str | None,
        active: bool | None,
        power: float | None,
        period: tuple[float, float],
    ) -> ControlView:
        st, s = self.state, self.settings
        # Outside warmth is measured from the maximum, cool air from the minimum.
        period_low, period_high = period
        idle_label = s[CONF_IDLE_HVAC_MODE].replace("_", " ")
        note = f" ({ahead})" if ahead else ""
        waiting_until: datetime | None = None

        if self.profile.hvac_mode == HVAC_MODE_OFF:
            state, status = STATE_OFF, "Profile off – AC off"
        elif room is None:
            state = CONTROL_UNAVAILABLE
            status = f"Room sensor unavailable – {idle_label}"
        elif d.mode == MODE_HEAT:
            state = STATE_HEATING
            status = f"Heating mode – holding {_t(target)} °C{note}"
        elif d.mode == MODE_COOL:
            state = STATE_COOLING
            status = f"Cooling mode – holding {_t(target)} °C{note}"
        elif d.held:
            state = STATE_WAITING
            hard = s[CONF_HARD_MARGIN]
            if d.warm_hold and d.low is not None:
                warm = period_high + s[CONF_WARMTH_MARGIN]
                status = (
                    f"Near minimum – warm outside (above {_t(warm)} °C): "
                    f"heating only below {_t(d.low - hard)} °C"
                )
            else:
                assert d.high is not None
                cool = period_low - s[CONF_COOL_MARGIN]
                status = (
                    f"Near maximum – cool outside (below {_t(cool)} °C): "
                    f"cooling only above {_t(d.high + hard)} °C"
                )
            status += note
        elif d.waiting:
            state = STATE_WAITING
            word = "Near minimum" if d.heat_zone else "Near maximum"
            if d.gap_until is not None:
                what = "heating" if d.heat_zone else "cooling"
                status = f"{word} – {what} possible from {_hm(d.gap_until)}"
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
        else:
            state = STATE_NEUTRAL
            if d.low is not None and d.high is not None:
                rng = f"{_t(d.low)}–{_t(d.high)} °C"
            elif d.low is not None:
                rng = f"≥ {_t(d.low)} °C"
            else:
                rng = f"≤ {_t(d.high)} °C"
            status = f"In range {rng}{note} – {idle_label}"
            if st.drift_until is not None and st.drift_side in ("heat", "cool"):
                heat = st.drift_side == "heat"
                limit = d.threshold_heat if heat else d.threshold_cool
                weather = "warm" if heat else "cool"
                status += (
                    f" ({weather} outside: may drift to {_t(limit)} °C "
                    f"until {_hm(st.drift_until)})"
                )
                waiting_until = dt_util.utc_from_timestamp(st.drift_until)

        attributes = {
            "mode": d.mode,
            "room": None if room is None else round(room, 2),
            "minimum": d.low,
            "maximum": d.high,
            "target": target,
            "ac_setpoint": setpoint,
            "offset_heat": st.offset_heat,
            "offset_cool": st.offset_cool,
            "ac_active": active,
            "ac_power": power,
            "trend_per_30min": round(trend, 2),
            "waited_min": round(d.waited),
            "warmth_coming": d.warmth_coming,
            "free_cooling": d.free_cooling,
            "look_ahead": ahead,
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
