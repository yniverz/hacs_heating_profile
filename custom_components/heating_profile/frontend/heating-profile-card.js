/*
 * Heating Profile card: a thermostat-style dial for a heating_profile climate
 * entity, plus day/night temperature range and start time controls.
 *
 * Served and loaded automatically by the heating_profile integration.
 */

const DOMAIN = "heating_profile";
const CARD_TYPE = "heating-profile-card";
const DEBOUNCE_MS = 800;

const MODES = [
  { mode: "heat", icon: "mdi:fire", label: "Heat" },
  { mode: "cool", icon: "mdi:snowflake", label: "Cool" },
  { mode: "heat_cool", icon: "mdi:sun-snowflake-variant", label: "Auto (range)" },
  { mode: "off", icon: "mdi:power", label: "Off" },
];

const MODE_COLORS = {
  heat: "var(--state-climate-heat-color, #ff8100)",
  cool: "var(--state-climate-cool-color, #2b9af9)",
  heat_cool: "var(--state-climate-heat_cool-color, #ffa600)",
  off: "var(--state-climate-off-color, var(--disabled-color, #8a8a8a))",
};

// low = minimum (heating aims for it), high = maximum (cooling aims for it).
const PERIODS = {
  day: { icon: "mdi:weather-sunny", label: "Day", low: "day_temp", high: "day_temp_high", start: "day_start" },
  night: { icon: "mdi:weather-night", label: "Night", low: "night_temp", high: "night_temp_high", start: "night_start" },
};

// Attribute -> field of heating_profile.set_profile
const FIELDS = {
  day_temp: "day_temperature",
  day_temp_high: "day_temperature_high",
  night_temp: "night_temperature",
  night_temp_high: "night_temperature_high",
};

// Which end of the range the dial and the single target follow per mode.
function activeEnds(mode) {
  if (mode === "heat") return ["low"];
  if (mode === "cool") return ["high"];
  return ["low", "high"];
}

// Dial geometry: a 270° arc opening at the bottom.
const CX = 100;
const CY = 100;
const R = 82;
const ARC_START = 135;
const ARC_SWEEP = 270;

function polar(angle) {
  const rad = (angle * Math.PI) / 180;
  return [CX + R * Math.cos(rad), CY + R * Math.sin(rad)];
}

function arcPath(from, to) {
  if (to - from < 0.01) return "";
  const [x1, y1] = polar(from);
  const [x2, y2] = polar(to);
  const large = to - from > 180 ? 1 : 0;
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${R} ${R} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function formatTemp(value) {
  return Number(value).toFixed(1);
}

// "06:00:00" or an ISO datetime in HA's local time -> "06:00"
function hhmm(value) {
  if (!value) return "";
  const match = String(value).match(/(\d{2}:\d{2})(:\d{2})?(?:[.+\-Z]|$)/);
  return match ? match[1] : "";
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; box-sizing: border-box; height: 100%; }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; padding: 0; }
  button:focus-visible, input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { font-size: 1.25rem; font-weight: 500; line-height: 1.4; color: var(--primary-text-color);
           text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .more { color: var(--secondary-text-color); display: flex; padding: 4px; border-radius: 50%; }
  .dial-wrap { position: relative; width: 100%; max-width: 260px; margin: 4px auto 0; }
  svg { display: block; width: 100%; height: auto; }
  .track { fill: none; stroke: var(--divider-color, rgba(127,127,127,.25)); stroke-width: 12; stroke-linecap: round; }
  .value { fill: none; stroke: var(--mode-color); stroke-width: 12; stroke-linecap: round; transition: stroke .3s; }
  .knob { fill: var(--card-background-color, #fff); stroke: var(--mode-color); stroke-width: 4; }
  .center { position: absolute; inset: 0; display: flex; flex-direction: column;
            align-items: center; justify-content: center; pointer-events: none; }
  .center > * { pointer-events: auto; }
  .target { font-size: 3rem; font-weight: 400; line-height: 1; color: var(--primary-text-color);
            font-variant-numeric: tabular-nums; }
  .target .unit { font-size: 1.25rem; vertical-align: top; margin-left: 2px; color: var(--secondary-text-color); }
  .target.range { font-size: 2.1rem; }
  .period { display: flex; align-items: center; gap: 4px; margin-top: 8px; padding: 4px 10px;
            border-radius: 16px; background: var(--secondary-background-color, rgba(127,127,127,.12));
            color: var(--primary-text-color); font-size: .9rem; }
  .period ha-icon { --mdc-icon-size: 18px; color: var(--mode-color); }
  .until { margin-top: 6px; font-size: .8rem; color: var(--secondary-text-color); min-height: 1em; }
  .dial-buttons { position: absolute; left: 0; right: 0; bottom: 4%; display: flex; justify-content: center; gap: 56px; }
  .round { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
           border: 1px solid var(--divider-color, rgba(127,127,127,.3)); color: var(--primary-text-color); }
  .round:hover, .step:hover { background: var(--secondary-background-color, rgba(127,127,127,.12)); }
  .modes { display: flex; justify-content: center; gap: 12px; margin: 12px 0 8px; }
  .mode { width: 48px; height: 48px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
          color: var(--secondary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.12));
          transition: background .2s, color .2s; }
  .mode.active { color: #fff; background: var(--active-color); }
  .rows { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; padding: 8px; border-radius: 12px; }
  .row.active { background: var(--secondary-background-color, rgba(127,127,127,.12)); }
  .row > ha-icon { color: var(--secondary-text-color); flex: none; }
  .row.active > ha-icon { color: var(--mode-color); }
  .label { flex: 1 1 auto; min-width: 8.5em; }
  .name { color: var(--primary-text-color); font-weight: 500; }
  .starts { display: flex; align-items: center; gap: 6px; font-size: .85rem; color: var(--secondary-text-color); }
  input[type=time] { font: inherit; color: var(--primary-text-color); background: transparent;
                     border: 1px solid var(--divider-color, rgba(127,127,127,.3)); border-radius: 6px; padding: 2px 4px; }
  .limits { display: flex; flex-direction: column; gap: 2px; flex: none; margin-left: auto; }
  .limit { display: flex; align-items: center; gap: 4px; transition: opacity .2s; }
  .limit.dim { opacity: .45; }
  .cap { font-size: .75rem; color: var(--secondary-text-color); width: 2.2em; text-align: right; }
  .stepper { display: flex; align-items: center; gap: 4px; flex: none; }
  .step { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
          color: var(--primary-text-color); }
  .step ha-icon, .round ha-icon { --mdc-icon-size: 20px; }
  .val { min-width: 3.6em; text-align: center; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
  .warning { padding: 8px; color: var(--error-color, #db4437); }
  .off .value { stroke: var(--disabled-color, #8a8a8a); }
`;

class HeatingProfileCard extends HTMLElement {
  constructor() {
    super();
    this._pending = {};
    this._timer = undefined;
    this.attachShadow({ mode: "open" });
  }

  static getConfigElement() {
    return document.createElement(`${CARD_TYPE}-editor`);
  }

  static getStubConfig(hass) {
    const found = Object.values(hass.entities || {}).find(
      (e) => e.platform === DOMAIN && e.entity_id.startsWith("climate.")
    );
    return { entity: found ? found.entity_id : "" };
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Select a Heating Profile climate entity");
    }
    if (!String(config.entity).startsWith("climate.")) {
      throw new Error("The entity must be a climate entity of Heating Profile");
    }
    this._config = config;
    this._build();
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  _build() {
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="header">
          <button class="title"></button>
          <button class="more" aria-label="More info"><ha-icon icon="mdi:dots-vertical"></ha-icon></button>
        </div>
        <div class="warning" hidden></div>
        <div class="body">
          <div class="dial-wrap">
            <svg viewBox="0 0 200 200" aria-hidden="true">
              <path class="track" d="${arcPath(ARC_START, ARC_START + ARC_SWEEP)}"></path>
              <path class="value"></path>
              <circle class="knob" data-knob="low" r="7"></circle>
              <circle class="knob" data-knob="high" r="7"></circle>
            </svg>
            <div class="center">
              <div class="target"><span class="target-value"></span><span class="unit"></span></div>
              <button class="period" aria-label="Switch day/night"><ha-icon></ha-icon><span></span></button>
              <div class="until"></div>
            </div>
            <div class="dial-buttons">
              <button class="round" data-dial="-1" aria-label="Lower target"><ha-icon icon="mdi:minus"></ha-icon></button>
              <button class="round" data-dial="1" aria-label="Raise target"><ha-icon icon="mdi:plus"></ha-icon></button>
            </div>
          </div>
          <div class="modes">
            ${MODES.map(
              (m) => `<button class="mode" data-mode="${m.mode}" title="${m.label}" aria-label="${m.label}"
                        style="--active-color:${MODE_COLORS[m.mode]}"><ha-icon icon="${m.icon}"></ha-icon></button>`
            ).join("")}
          </div>
          <div class="rows">
            ${Object.entries(PERIODS)
              .map(
                ([key, p]) => `
              <div class="row" data-period="${key}">
                <ha-icon icon="${p.icon}"></ha-icon>
                <div class="label">
                  <div class="name">${p.label}</div>
                  <label class="starts">starts <input type="time" data-start="${p.start}"></label>
                </div>
                <div class="limits">
                  ${[
                    ["low", "min", "minimum"],
                    ["high", "max", "maximum"],
                  ]
                    .map(
                      ([end, cap, word]) => `
                  <div class="limit" data-end="${end}">
                    <span class="cap">${cap}</span>
                    <div class="stepper">
                      <button class="step" data-attr="${p[end]}" data-dir="-1" aria-label="Lower ${p.label.toLowerCase()} ${word}"><ha-icon icon="mdi:minus"></ha-icon></button>
                      <span class="val" data-val="${p[end]}"></span>
                      <button class="step" data-attr="${p[end]}" data-dir="1" aria-label="Raise ${p.label.toLowerCase()} ${word}"><ha-icon icon="mdi:plus"></ha-icon></button>
                    </div>
                  </div>`
                    )
                    .join("")}
                </div>
              </div>`
              )
              .join("")}
          </div>
        </div>
      </ha-card>
    `;

    root.querySelector(".title").addEventListener("click", () => this._moreInfo());
    root.querySelector(".more").addEventListener("click", () => this._moreInfo());
    root.querySelector(".period").addEventListener("click", () => this._togglePeriod());
    root.querySelectorAll("[data-dial]").forEach((btn) =>
      btn.addEventListener("click", () => this._stepActive(Number(btn.dataset.dial)))
    );
    root.querySelectorAll(".mode").forEach((btn) =>
      btn.addEventListener("click", () => this._setMode(btn.dataset.mode))
    );
    root.querySelectorAll(".step").forEach((btn) =>
      btn.addEventListener("click", () => this._stepSetting(btn.dataset.attr, Number(btn.dataset.dir)))
    );
    root.querySelectorAll("input[type=time]").forEach((input) =>
      input.addEventListener("change", () => {
        if (!input.value) return;
        this._callProfile({ [input.dataset.start]: `${input.value}:00` });
      })
    );
  }

  get _stateObj() {
    return this._hass && this._config ? this._hass.states[this._config.entity] : undefined;
  }

  _limits(attrs) {
    return {
      min: Number(attrs.min_temp ?? 5),
      max: Number(attrs.max_temp ?? 30),
      step: Number(attrs.target_temp_step ?? 0.5),
    };
  }

  _update() {
    const root = this.shadowRoot;
    if (!root || !this._config || !this._hass || !root.querySelector("ha-card")) return;
    const stateObj = this._stateObj;
    const warning = root.querySelector(".warning");
    const body = root.querySelector(".body");
    const title = root.querySelector(".title");

    if (!stateObj || stateObj.state === "unavailable") {
      title.textContent = this._config.name || this._config.entity;
      warning.textContent = stateObj ? "Entity is unavailable" : `Entity not found: ${this._config.entity}`;
      warning.hidden = false;
      body.hidden = true;
      return;
    }
    warning.hidden = true;
    body.hidden = false;

    const a = stateObj.attributes;
    const mode = stateObj.state;
    const period = a.period || a.preset_mode || "day";
    const periodInfo = PERIODS[period] || PERIODS.day;
    const { min, max } = this._limits(a);
    const low = this._value(periodInfo.low, a);
    const high = this._value(periodInfo.high, a);
    const ends = activeEnds(mode);
    const isRange = ends.length === 2;
    const card = root.querySelector("ha-card");

    card.style.setProperty("--mode-color", MODE_COLORS[mode] || MODE_COLORS.heat);
    card.classList.toggle("off", mode === "off");
    title.textContent = this._config.name || a.friendly_name || this._config.entity;

    // Dial: an arc from the start to the target, or across the whole range.
    const frac = (v) => Math.min(1, Math.max(0, (Number(v) - min) / (max - min || 1)));
    const single = mode === "cool" ? high : low;
    const from = isRange ? ARC_START + ARC_SWEEP * frac(low) : ARC_START;
    const to = ARC_START + ARC_SWEEP * frac(isRange ? high : single);
    root.querySelector(".value").setAttribute("d", arcPath(from, to));
    const place = (knob, angle, show) => {
      const [x, y] = polar(angle);
      knob.setAttribute("cx", x.toFixed(2));
      knob.setAttribute("cy", y.toFixed(2));
      knob.style.display = show ? "" : "none";
    };
    place(root.querySelector('[data-knob="low"]'), isRange ? from : to, true);
    place(root.querySelector('[data-knob="high"]'), to, isRange);
    const targetEl = root.querySelector(".target");
    targetEl.classList.toggle("range", isRange);
    root.querySelector(".target-value").textContent = isRange
      ? `${low == null ? "–" : formatTemp(low)}–${high == null ? "–" : formatTemp(high)}`
      : single == null
        ? "–"
        : formatTemp(single);
    root.querySelector(".unit").textContent = this._hass.config?.unit_system?.temperature || "°C";

    const periodBtn = root.querySelector(".period");
    periodBtn.querySelector("ha-icon").setAttribute("icon", periodInfo.icon);
    periodBtn.querySelector("span").textContent = periodInfo.label;
    const until = hhmm(a.next_switch);
    root.querySelector(".until").textContent =
      mode === "off" ? "Off" : until ? `${a.override ? "Manual until" : "until"} ${until}` : "";

    // Modes
    root.querySelectorAll(".mode").forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", String(active));
    });

    // Day/night rows; the end of the range the mode does not use is dimmed.
    root.querySelectorAll(".row").forEach((row) => {
      const info = PERIODS[row.dataset.period];
      row.classList.toggle("active", row.dataset.period === period);
      row.querySelectorAll(".limit").forEach((limit) => {
        const attr = info[limit.dataset.end];
        const value = this._value(attr, a);
        limit.querySelector(".val").textContent = value == null ? "–" : `${formatTemp(value)} °C`;
        limit.classList.toggle("dim", mode !== "off" && !ends.includes(limit.dataset.end));
      });
      const input = row.querySelector("input[type=time]");
      if (this.shadowRoot.activeElement !== input) input.value = hhmm(a[info.start]);
    });
  }

  _value(attr, attrs) {
    return this._pending[attr] ?? attrs[attr];
  }

  _clamp(value, attrs) {
    const { min, max, step } = this._limits(attrs);
    const rounded = Math.round(value / step) * step;
    return Math.min(max, Math.max(min, Number(rounded.toFixed(2))));
  }

  // Collect changes to several settings and send them in one call.
  _queue(changes) {
    Object.assign(this._pending, changes);
    this._update();
    clearTimeout(this._timer);
    this._timer = setTimeout(async () => {
      const pending = this._pending;
      this._pending = {};
      const data = {};
      for (const [attr, value] of Object.entries(pending)) data[FIELDS[attr]] = value;
      try {
        await this._callProfile(data);
      } finally {
        this._update();
      }
    }, DEBOUNCE_MS);
  }

  _stepActive(dir) {
    const stateObj = this._stateObj;
    if (!stateObj) return;
    const a = stateObj.attributes;
    const info = PERIODS[a.period || a.preset_mode] || PERIODS.day;
    const step = this._limits(a).step;
    // Heat moves the minimum, cool the maximum, heat_cool and off the range.
    const changes = {};
    for (const end of activeEnds(stateObj.state)) {
      const attr = info[end];
      changes[attr] = this._clamp(Number(this._value(attr, a)) + dir * step, a);
    }
    this._queue(changes);
  }

  _stepSetting(attr, dir) {
    const stateObj = this._stateObj;
    if (!stateObj) return;
    const a = stateObj.attributes;
    const next = this._clamp(Number(this._value(attr, a)) + dir * this._limits(a).step, a);
    this._queue({ [attr]: next });
  }

  _callProfile(data) {
    return this._hass.callService(DOMAIN, "set_profile", {
      entity_id: this._config.entity,
      ...data,
    });
  }

  _setMode(mode) {
    this._hass.callService("climate", "set_hvac_mode", {
      entity_id: this._config.entity,
      hvac_mode: mode,
    });
  }

  _togglePeriod() {
    const stateObj = this._stateObj;
    if (!stateObj) return;
    const period = stateObj.attributes.period || stateObj.attributes.preset_mode;
    this._hass.callService("climate", "set_preset_mode", {
      entity_id: this._config.entity,
      preset_mode: period === "day" ? "night" : "day",
    });
  }

  _moreInfo() {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId: this._config.entity },
        bubbles: true,
        composed: true,
      })
    );
  }
}

class HeatingProfileCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.schema = [
        {
          name: "entity",
          required: true,
          selector: { entity: { domain: "climate", integration: DOMAIN } },
        },
        { name: "name", selector: { text: {} } },
      ];
      this._form.computeLabel = (s) => (s.name === "entity" ? "Heating profile" : "Name (optional)");
      this._form.addEventListener("value-changed", (ev) => {
        const config = { ...ev.detail.value };
        if (!config.name) delete config.name;
        this.dispatchEvent(
          new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true })
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
  }
}

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, HeatingProfileCard);
  customElements.define(`${CARD_TYPE}-editor`, HeatingProfileCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TYPE,
    name: "Heating Profile",
    description: "Thermostat dial with day/night temperature ranges and start times.",
    preview: true,
    documentationURL: "https://github.com/yniverz/hacs_heating_profile",
  });
}
