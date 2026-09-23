# Heating Profile

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/yniverz/hacs_heating_profile/actions/workflows/validate.yml/badge.svg)](https://github.com/yniverz/hacs_heating_profile/actions/workflows/validate.yml)
[![Tests](https://github.com/yniverz/hacs_heating_profile/actions/workflows/tests.yml/badge.svg)](https://github.com/yniverz/hacs_heating_profile/actions/workflows/tests.yml)

A virtual thermostat per room: one device with a **climate entity** that holds
a day temperature, a night temperature, the times when day and night start,
and a Heat / Cool / Off mode. It comes with its own **dashboard card**.

It **controls nothing** by itself. It stores the values (they survive
restarts) and shows the temperature that should be active right now, so your
own automations can drive real thermostats, TRVs or air conditioners.

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

- **A dial** with the active target temperature. Use − / + to change the
  temperature of the current period.
- **A Day / Night badge**. Tap it to switch to the other period until the next
  scheduled switch ("Manual until 22:00").
- **Heat, Cool and Off** buttons.
- **Day and Night rows**, each with its temperature (− / +) and start time.

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
| `number.living_room_day_temperature` | 21.0 °C | configuration, 5–30 °C, step 0.5 |
| `number.living_room_night_temperature` | 17.0 °C | configuration, 5–30 °C, step 0.5 |
| `time.living_room_day_starts` | 06:00 | configuration |
| `time.living_room_night_starts` | 22:00 | configuration |

The four settings entities are **configuration entities**. They appear under
the device's *Configuration* section and are left out of auto-generated
dashboards, but automations can still use them.

### Climate entity

- **State / HVAC mode:** `heat`, `cool` or `off`. It's only stored, so your
  automations decide what each mode means. Turning it on again restores the
  last mode (heat or cool).
- **Target temperature:** the day temperature between *Day starts* and *Night
  starts*, otherwise the night temperature. The day range may cross midnight
  (e.g. day 20:00 → night 04:00). If both times are equal, it is always night.
  Setting the target (from the card, the Thermostat card or
  `climate.set_temperature`) changes the temperature of the **current** period.
- **Preset:** `day` or `night`, always the current period. Choosing the other
  preset forces it until the next scheduled switch. After that the schedule
  takes over again. Choosing the scheduled period again cancels the override.
  Changing a start time also cancels it.
- It re-evaluates at the start of every minute and immediately when a setting
  changes.

| Attribute | Example | |
| --- | --- | --- |
| `temperature` | `21.0` | active target (standard climate attribute) |
| `preset_mode` | `day` | standard climate attribute |
| `period` | `day` / `night` | same as the preset |
| `day_temp` | `21.0` | |
| `night_temp` | `17.0` | |
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
  day_temperature: 21.5
  night_temperature: 17
  day_start: "06:30"
  night_start: "22:30"
```

The configuration entities work too: `number.set_value` and `time.set_value`.

## Automation examples

Follow the profile with a real thermostat, respecting the mode:

```yaml
automation:
  - alias: "Living room: follow heating profile"
    triggers:
      # Without to/from this fires on mode and attribute (target) changes.
      - trigger: state
        entity_id: climate.living_room
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
{{ state_attr('climate.living_room', 'temperature') }}         {# active target #}
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
