# Heating Profile

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/yniverz/hacs_heating_profile/actions/workflows/validate.yml/badge.svg)](https://github.com/yniverz/hacs_heating_profile/actions/workflows/validate.yml)
[![Tests](https://github.com/yniverz/hacs_heating_profile/actions/workflows/tests.yml/badge.svg)](https://github.com/yniverz/hacs_heating_profile/actions/workflows/tests.yml)

A Home Assistant helper that gives each room a small **settings panel as a device**:
a day temperature, a night temperature, and the times when day and night start.

It **controls nothing**. It only stores the values (they survive restarts) and
exposes a sensor with the temperature that is active right now, so your own
automations can read it.

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

## Entities

Each entry creates one device (manufacturer *Custom*, model *Heating profile*).
Entity IDs are derived from the name; for a profile called `Living room`:

| Entity | Type | Default | Notes |
| --- | --- | --- | --- |
| `number.living_room_day_temperature` | number | 21.0 °C | 5–30 °C, step 0.5 |
| `number.living_room_night_temperature` | number | 17.0 °C | 5–30 °C, step 0.5 |
| `time.living_room_day_starts` | time | 06:00 | |
| `time.living_room_night_starts` | time | 22:00 | |
| `sensor.living_room_target_temperature` | sensor | – | the active temperature |

### Target temperature sensor

State: the day temperature between *Day starts* and *Night starts*, otherwise
the night temperature. The day range may cross midnight (e.g. day 20:00 →
night 04:00). If both times are equal the profile is always in night mode.

The sensor re-evaluates at the start of every minute and immediately whenever
a setting changes.

| Attribute | Example |
| --- | --- |
| `period` | `day` or `night` |
| `day_temp` | `21.0` |
| `night_temp` | `17.0` |
| `day_start` | `06:00:00` |
| `night_start` | `22:00:00` |

## Automation examples

Set a thermostat whenever the target changes:

```yaml
automation:
  - alias: "Living room: follow heating profile"
    triggers:
      - trigger: state
        entity_id: sensor.living_room_target_temperature
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: "{{ states('sensor.living_room_target_temperature') | float }}"
```

React to the switch between day and night using the `period` attribute:

```yaml
automation:
  - alias: "Bedroom: night mode"
    triggers:
      - trigger: state
        entity_id: sensor.bedroom_target_temperature
        attribute: period
        to: "night"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.bedroom
        data:
          temperature: "{{ state_attr('sensor.bedroom_target_temperature', 'night_temp') }}"
```

Use the values in a template condition:

```yaml
conditions:
  - condition: template
    value_template: >
      {{ state_attr('sensor.living_room_target_temperature', 'period') == 'day'
         and states('sensor.living_room_temperature') | float(0)
             < state_attr('sensor.living_room_target_temperature', 'day_temp') }}
```

Boost the day temperature from a script:

```yaml
action: number.set_value
target:
  entity_id: number.living_room_day_temperature
data:
  value: 22.5
```

## Storage

Values are stored per entry in `.storage/heating_profile.<entry_id>` and the
file is deleted when you remove the entry.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

## License

[MIT](LICENSE)
