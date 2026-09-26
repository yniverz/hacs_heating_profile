# Heating Profile

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/yniverz/hacs_heating_profile/actions/workflows/validate.yml/badge.svg)](https://github.com/yniverz/hacs_heating_profile/actions/workflows/validate.yml)
[![Tests](https://github.com/yniverz/hacs_heating_profile/actions/workflows/tests.yml/badge.svg)](https://github.com/yniverz/hacs_heating_profile/actions/workflows/tests.yml)

A virtual thermostat per room: one device with a **climate entity** that holds
a day and a night **temperature range** (minimum and maximum), the times when
day and night start, and a Heat / Cool / Auto (range) / Off mode. It comes with
its own **dashboard card**.

On its own it only stores the values (they survive restarts) and shows the
range that applies right now, so your automations can drive thermostats or
TRVs. Optionally it drives an **air conditioner** itself: pick a room sensor
and the AC under *Configure* and it keeps the room in the range, see
[Climate control](#climate-control).

## Installation

### HACS (custom repository)

1. In Home Assistant open **HACS**.
2. Open the menu (⋮, top right) → **Custom repositories**.
3. Add `https://github.com/yniverz/hacs_heating_profile` with type **Integration**.
4. Search for **Heating Profile**, open it and click **Download**.
5. Restart Home Assistant.

### Manual

1. Download the latest release (or clone this repository).
2. Copy `custom_components/heating_profile` into the `custom_components`
   folder of your Home Assistant configuration directory, so you end up with
   `<config>/custom_components/heating_profile/manifest.json`.
3. Restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → Heating Profile.**
The only field is the name (default `Heating profile`). Add one entry per room,
e.g. `Living room`, `Bedroom`.

## The dashboard card

The card is installed with the integration. You don't need to add a resource
yourself. Edit a dashboard → **Add card** → search for **Heating Profile** and
pick the room. Or use YAML:

```yaml
type: custom:heating-profile-card
entity: climate.living_room
name: Living room   # optional
```

The card shows:

- **A dial** with the active target: the minimum in Heat, the maximum in Cool,
  the whole range in Auto and Off. − / + changes the current period's
  minimum, maximum or moves the whole range, respectively.
- **A Day / Night badge**. Tap it to switch to the other period until the next
  scheduled switch ("Manual until 22:00").
- **Heat, Cool, Auto (range) and Off** buttons.
- **Day and Night rows**, each with its minimum and maximum (− / +) and start
  time. The end of the range the current mode doesn't use is dimmed.

If the card doesn't show up right after installing or updating, reload the
browser page (on the mobile app: Settings → Companion app → Debugging →
Reset frontend cache).

The climate entity also works with the built-in **Thermostat** card, voice
assistants and anything else that understands climate entities.

## Entities

Each entry creates one device (manufacturer *Custom*, model *Heating profile*).
Entity IDs come from the name; for a profile called `Living room`:

| Entity | Default | Notes |
| --- | --- | --- |
| `climate.living_room` | Heat, 21.0 °C | the main entity (see below) |
| `number.living_room_day_minimum` | 21.0 °C | configuration, 5–30 °C, step 0.5 |
| `number.living_room_day_maximum` | 25.0 °C | configuration, 5–30 °C, step 0.5 |
| `number.living_room_night_minimum` | 17.0 °C | configuration, 5–30 °C, step 0.5 |
| `number.living_room_night_maximum` | 24.0 °C | configuration, 5–30 °C, step 0.5 |
| `time.living_room_day_starts` | 06:00 | configuration |
| `time.living_room_night_starts` | 22:00 | configuration |

Minimum and maximum always stay at least 1 °C apart: if you move one past the
other, the other one is pushed along.

The six settings entities are **configuration entities**. They appear under
the device's *Configuration* section and are left out of auto-generated
dashboards, but automations can still use them.

### Climate entity

- **State / HVAC mode:** `heat`, `cool`, `heat_cool` (shown as *Auto* in the
  card) or `off`. It's only stored, so your automations decide what each mode
  means. The intended meaning: heat up to the minimum, cool down to the
  maximum, or keep the room inside the range with either. Turning it on again
  restores the last mode (heat, cool or heat_cool).
- **Period:** day between *Day starts* and *Night starts*, otherwise night.
  The day range may cross midnight (e.g. day 20:00 → night 04:00). If both
  times are equal, it is always night.
- **Target:** follows the mode and the current period.

  | Mode | `temperature` | `target_temp_low` / `target_temp_high` |
  | --- | --- | --- |
  | `heat` | minimum | – |
  | `cool` | maximum | – |
  | `heat_cool` | – | minimum / maximum (the Thermostat card shows two handles) |
  | `off` | – | – |

  Changing the target (from the card, the Thermostat card or
  `climate.set_temperature`) changes the **current** period: `temperature`
  sets the minimum (heat, off) or the maximum (cool), or centers the range on
  it (heat_cool); `target_temp_low` / `target_temp_high` set both ends.
- **Preset:** `day` or `night`, always the current period. Choosing the other
  preset forces it until the next scheduled switch. After that the schedule
  takes over again. Choosing the scheduled period again cancels the override.
  Changing a start time also cancels it.
- It re-evaluates at the start of every minute and immediately when a setting
  changes.

| Attribute | Example | |
| --- | --- | --- |
| `temperature` | `21.0` | active target in heat/cool (standard climate attribute) |
| `target_temp_low` / `target_temp_high` | `21.0` / `25.0` | active range in heat_cool (standard climate attributes) |
| `preset_mode` | `day` | standard climate attribute |
| `period` | `day` / `night` | same as the preset |
| `day_temp` / `day_temp_high` | `21.0` / `25.0` | day minimum / maximum, in every mode |
| `night_temp` / `night_temp_high` | `17.0` / `24.0` | night minimum / maximum, in every mode |
| `day_start` | `06:00:00` | |
| `night_start` | `22:00:00` | |
| `override` | `false` | `true` while a manual day/night override is active |
| `next_switch` | `2026-01-15T22:00:00+01:00` | when the period changes next |

## Actions

Besides the standard climate actions (`climate.set_temperature`,
`climate.set_hvac_mode`, `climate.set_preset_mode`, `climate.turn_on` /
`turn_off`) there is one action to change several settings at once. Every
field is optional:

```yaml
action: heating_profile.set_profile
target:
  entity_id: climate.living_room
data:
  day_temperature: 21.5        # day minimum
  day_temperature_high: 25     # day maximum
  night_temperature: 17
  night_temperature_high: 23
  day_start: "06:30"
  night_start: "22:30"
```

The configuration entities work too: `number.set_value` and `time.set_value`.

## Automation examples

Follow the profile with a real thermostat (heat/cool/off):

```yaml
automation:
  - alias: "Living room: follow heating profile"
    triggers:
      # Without to/from this fires on mode and attribute (target) changes.
      - trigger: state
        entity_id: climate.living_room
    conditions:
      - condition: state
        entity_id: climate.living_room
        state: [heat, cool, "off"]
    actions:
      - action: climate.set_hvac_mode
        target:
          entity_id: climate.living_room_trv
        data:
          hvac_mode: "{{ states('climate.living_room') }}"
      - if: "{{ not is_state('climate.living_room', 'off') }}"
        then:
          - action: climate.set_temperature
            target:
              entity_id: climate.living_room_trv
            data:
              temperature: "{{ state_attr('climate.living_room', 'temperature') }}"
```

In `heat_cool`, read the range of the current period with
`state_attr('climate.living_room', 'target_temp_low')` and `'target_temp_high'`
and decide yourself whether to heat, cool or do nothing.

React to the switch between day and night:

```yaml
automation:
  - alias: "Bedroom: night starts"
    triggers:
      - trigger: state
        entity_id: climate.bedroom
        attribute: period
        to: "night"
    actions:
      - action: notify.notify
        data:
          message: "Bedroom switched to {{ state_attr('climate.bedroom', 'night_temp') }} °C"
```

Templates:

```jinja
{{ state_attr('climate.living_room', 'period') }}              {# day / night #}
{{ is_state_attr('climate.living_room', 'period', 'day') }}    {# true / false #}
{{ state_attr('climate.living_room', 'temperature') }}         {# target in heat/cool #}
{{ state_attr('climate.living_room', 'target_temp_low') }}     {# minimum in heat_cool #}
{{ is_state('climate.living_room', 'heat') }}                  {# heating mode? #}
```

Weekend lie-in from an automation:

```yaml
action: heating_profile.set_profile
target:
  entity_id: climate.living_room
data:
  day_start: "{{ '08:00' if now().weekday() >= 5 else '06:00' }}"
```

## Upgrading from 0.3.x

- Each period got a **maximum**; your existing temperatures become the
  minimum. New maximums default to 25 °C (day) and 24 °C (night), or 1 °C
  above the minimum if that's higher.
- The **Auto** mode is now the standard `heat_cool` mode and holds the range.
  A stored `auto` is converted automatically; automations that set
  `hvac_mode: auto` need to use `heat_cool`.
- `temperature` is now empty in heat_cool and off; use `target_temp_low` /
  `target_temp_high`, or `day_temp` / `night_temp`, which are always set.
- Existing entities keep their entity IDs (e.g.
  `number.living_room_day_temperature`); only their names change to
  *Day minimum* / *Night minimum*.

## Climate control

**Settings → Devices & services → Heating Profile → Configure** (per profile).

### Sources

| Field | |
| --- | --- |
| Room temperature sensor | The control decides only on this sensor. Required. |
| Air conditioner | Climate entity that gets the commands. Required. |
| AC power sensor | Shows whether the AC works and how hard (leaving a mode, learning). This or the compressor sensor is required. |
| Compressor running sensor | Used instead of the power sensor if that isn't set. |
| Use the weather forecast | Hourly Open-Meteo forecast for the Home Assistant location (no API key). |

Leave room sensor and AC empty to turn the control off again; its entities
are removed, the other settings are kept.

### How it works

The control works in two layers and lets the AC modulate by itself:

**1. Mode (neutral / heat / cool), changed rarely.** Neutral runs the idle
mode (e.g. fan only, silent).

| Profile mode | Modes used |
| --- | --- |
| Auto (range) | heat below, cool above the range, neutral in between |
| Heat / Cool | only heat / only cool |
| Off | the AC is switched off |

- **Neutral → heat** when the room (10-min average) reaches the minimum +
  0.1 °C, unless free warmth is coming: at least minimum + 1 °C outside for
  the whole next hour, or sun ≥ 250 W/m². It waits at most 60 min, and not
  while the room falls 0.3 °C per 30 min or faster. Cool mode mirrored (maximum
  − 0.1 °C; at most maximum − 2 °C outside).
- **Heat → neutral** when the AC has idled for 60 min (power sensor or
  compressor), the room is at the target and the mode has lasted 2 h.
- **Early switch to neutral:** if it stays really warm outside (minimum +
  3 °C for the next 2 h, or sun ≥ 400 W/m²) and the room is at least at the
  minimum. The room may then drift 0.3 °C below the minimum for up to 60 min
  before heat mode comes back. Cooling mirrored.
- **Protection:** at least 30 min between neutral and heat/cool, 6 h between
  heating and cooling; 1.5 °C beyond the range switches right away.
- **Look ahead:** 30 min before a day/night switch (or the end of a manual
  override) the next period's range applies: pre-heat/-cool for it, skip
  what the old range no longer needs.

**2. Setpoint.** Heat mode holds minimum + 0.3 °C, cool mode maximum −
0.3 °C. The AC gets that target plus (heating) or minus (cooling) an
**offset** that makes up for its own sensor (it sits in its own air stream).
The offset is learned from the room sensor every 20 min: raised when the room
stays too cold while the AC idles or works gently, lowered when it's too warm
while the AC works. Nothing is learned during warm-up (high power), while the
AC cycles by itself, far from the target, or while the room is still changing
(current reading, 10-min and 30-min average must agree). Starting value
2 °C, range −2 to +6 °C, at most 0.5 °C per step.

**Also:** a change on the AC the control didn't send (remote, app, another
automation) pauses the control for 2 h; *End pause* or switching the control
off and on resumes it. The same command is repeated at most every 10 min.
Room sensor unavailable: neutral. AC unavailable: nothing is sent.

Every number above is a setting in the second step of *Configure*.

### Entities

For a profile called `Living room`:

| Entity | |
| --- | --- |
| `switch.living_room_climate_control` | control on/off (off: nothing is sent to the AC) |
| `button.living_room_end_pause` | end a pause after a manual change |
| `sensor.living_room_control_status` | what it does, e.g. `Heating mode – holding 21.3 °C`; only changes when the situation changes, live values are attributes |
| `sensor.living_room_control_reason` | why the current heat/cool mode started; empty in neutral |
| `sensor.living_room_control_state` | `disabled`, `unavailable`, `paused`, `off`, `neutral`, `waiting`, `heating`, `cooling` |
| `number.living_room_heating_offset`, `number.living_room_cooling_offset` | learned offsets (can be set by hand) |
| diagnostic sensors (disabled by default) | room average, room trend, forecast outside minimum/maximum and radiation for the next waiting window, AC setpoint, waiting until, paused until |

## Upgrading from 0.6.x

The on/off control (heat to the middle of the range, then fan) is replaced
by the mode + setpoint control above. Presence is gone. Learned offsets are
kept. Open *Configure* once: choose the AC power sensor and check the new
settings (the old ones are dropped when you save).

## Storage

Values are stored per entry in `.storage/heating_profile.<entry_id>`, the
state of the climate control in `.storage/heating_profile.<entry_id>.control`.
Both files are deleted when you remove the entry.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

## License

[MIT](LICENSE)
