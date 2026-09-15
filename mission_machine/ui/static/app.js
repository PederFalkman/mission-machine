"use strict";

/* Mission Machine - Pack 1 demonstrator UI.
   Plain DOM, no framework. Four screens, one in-memory session on the server. */

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
};
const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };
const pct = (v) => `${Math.round(v * 100)}%`;
const h1 = (v) => `${Number(v).toFixed(1)}`;
const h0 = (v) => `${Math.round(Number(v))}`;

const api = {
  async get(path) { const r = await fetch(path); return r.json(); },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return r.json();
  },
};

const STATE = { mission: null, plan: null, recommendation: null, comparison: null, operate: null };

/* ---------------------------------------------------------------- nav */

for (const button of document.querySelectorAll("#nav button")) {
  button.addEventListener("click", () => {
    document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b === button));
    document.querySelectorAll(".view").forEach((v) => {
      v.classList.toggle("active", v.id === `view-${button.dataset.view}`);
    });
  });
}
const showView = (name) => document.querySelector(`#nav button[data-view="${name}"]`).click();

/* ------------------------------------------------------------- mission */

function renderMission(data) {
  STATE.mission = data;
  $("disclaimer").textContent = data.disclaimer;
  $("version").textContent = `mission-machine ${data.version} · ${data.mission.mission_id}`;

  const mission = data.mission;
  const loadsById = Object.fromEntries(data.assets.assets.map((a) => [a.asset_id, a]));

  $("flow-mission").textContent =
    `${mission.name} — hold ${mission.critical_loads.length} critical functions for ${h0(mission.mission_duration_h)} h`;
  $("flow-resources").textContent = data.assets.assets
    .filter((a) => !a.criticality)
    .map((a) => a.asset_id)
    .join(" · ");

  const loads = clear($("loads-table"));
  const intentFor = (id) => {
    const priority = mission.operator_priorities
      .filter((p) => p.is_readable && p.applies_to.includes(id))
      .sort((a, b) => a.rank - b.rank)[0];
    return priority ? `${priority.intent} (priority ${priority.rank})` : "not stated";
  };
  loads.append(el("tr", {}, el("th", {}, "Function"), el("th", {}, "Asset"), el("th", {}, "Class"), el("th", {}, "Operator intent")));
  for (const id of mission.critical_loads.concat(mission.secondary_loads)) {
    const load = loadsById[id];
    const critical = mission.critical_loads.includes(id);
    const intent = intentFor(id);
    loads.append(el("tr", {},
      el("td", {}, load.function || load.name),
      el("td", {}, id),
      el("td", {}, el("span", { class: `badge ${critical ? "yes" : ""}` }, critical ? "CRITICAL" : "SECONDARY")),
      el("td", { class: intent === "not stated" ? "note" : "" }, intent)));
  }

  const constraints = clear($("constraints-table"));
  const rows = [
    ["Mission duration", `${h0(mission.mission_duration_h)} h in ${h0(mission.time_step_h)} h steps`],
    ["Required critical availability", pct(mission.required_availability)],
    ["Minimum energy reserve", `${h0(mission.minimum_reserve.value)} h of critical load`],
    ["Fuel on site", `${h0(mission.fuel_limit_l)} L (no resupply assumed)`],
    ["Deployment time limit", `${h0(mission.deployment_time_limit_min)} min`],
    ["Grid availability", `${pct(data.derived.grid_availability_fraction)} of the mission`],
    ["Mean critical demand", `${h1(data.derived.mean_critical_demand_kw)} kW`],
    ["Critical energy required", `${h0(data.derived.critical_energy_demand_kwh)} kWh`],
    ["Could not move with the node", data.derived.mobility_excluded_assets.join(", ") || "none"],
    ["Location context", mission.location_context],
  ];
  for (const [key, value] of rows) {
    constraints.append(el("tr", {}, el("th", {}, key), el("td", {}, value)));
  }

  const assets = clear($("assets-table"));
  assets.append(el("tr", {},
    el("th", {}, "Asset"), el("th", {}, "Name"), el("th", { class: "num" }, "Power"),
    el("th", { class: "num" }, "Energy"), el("th", {}, "Mobility"),
    el("th", { class: "num" }, "Setup"), el("th", {}, "Evidence")));
  for (const asset of data.assets.assets.filter((a) => !a.criticality)) {
    assets.append(el("tr", {},
      el("td", {}, asset.asset_id),
      el("td", {}, asset.name),
      el("td", { class: "num" }, `${h0(asset.capacity_kw)} kW`),
      el("td", { class: "num" }, asset.energy_capacity_kwh ? `${h0(asset.energy_capacity_kwh)} kWh` : "—"),
      el("td", {}, asset.mobility),
      el("td", { class: "num" }, `${h0(asset.setup_time_min)} min`),
      el("td", {}, asset.data_labels.join(" · "))));
  }

  const priorities = clear($("priorities"));
  for (const priority of mission.operator_priorities) {
    const applies = priority.applies_to.length ? ` [${priority.applies_to.join(", ")}]` : "";
    const quantity = priority.quantity ? ` (${priority.quantity})` : "";
    priorities.append(el("li", {},
      priority.statement,
      el("span", { class: priority.is_readable ? "tag" : "tag advisory" },
        `${priority.intent}${quantity}${applies}`)));
  }
  const advisory = mission.operator_priorities.filter((p) => !p.is_readable);
  $("priorities-note").textContent = advisory.length
    ? `${advisory.length === 1 ? "Priority" : "Priorities"} ${advisory.map((p) => p.rank).join(", ")} ${advisory.length === 1 ? "is" : "are"} recorded and shown, but the planner has no way to act on ${advisory.length === 1 ? "it" : "them"}.`
    : "Every priority above is in a form the planner reads.";

  $("env-chart").replaceChildren(environmentChart(data.profiles));
  $("env-note").textContent =
    `${mission.weather_profile.description}. Grid: ${mission.grid_availability.note}`;

  const assumptions = clear($("assumptions-table"));
  assumptions.append(el("tr", {}, el("th", {}, "Id"), el("th", {}, "Assumption"), el("th", {}, "If wrong")));
  for (const assumption of data.assumptions) {
    assumptions.append(el("tr", {},
      el("td", {}, `${assumption.assumption_id} [${assumption.label}]`),
      el("td", {}, assumption.statement),
      el("td", {}, assumption.impact_if_wrong)));
  }

  const select = clear($("scenario-select"));
  for (const scenario of data.scenarios) {
    select.append(el("option", { value: scenario.scenario_id }, `${scenario.scenario_id} — ${scenario.name}`));
  }
  const worlds = clear($("world-select"));
  for (const world of data.worlds || []) {
    const option = el("option", { value: world }, `world: ${world}`);
    if (world === data.current_world) option.setAttribute("selected", "selected");
    worlds.append(option);
  }
}

/* --------------------------------------------------------------- charts */

const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function environmentChart(profiles) {
  const width = 900, height = 190, padLeft = 42, padBottom = 22, padTop = 10;
  const hours = profiles.hours;
  const maxKw = Math.max(...profiles.critical_demand_kw, ...profiles.secondary_demand_kw) * 1.15;
  const x = (i) => padLeft + (i / (hours.length - 1)) * (width - padLeft - 10);
  const y = (kw) => padTop + (1 - kw / maxKw) * (height - padTop - padBottom);
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });

  hours.forEach((hour, i) => {
    if (!profiles.grid_available[i]) return;
    root.append(svg("rect", {
      x: x(i), y: padTop, width: (width - padLeft - 10) / hours.length, height: height - padTop - padBottom,
      fill: "#2b3524", opacity: "0.8",
    }));
  });
  const line = (values, stroke, dash) => {
    const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    root.append(svg("path", { d, fill: "none", stroke, "stroke-width": "2", "stroke-dasharray": dash || "" }));
  };
  line(profiles.critical_demand_kw, "#c9503f");
  line(profiles.secondary_demand_kw, "#97a08c", "4 3");
  line(profiles.solar_fraction.map((f) => f * maxKw * 0.4), "#b8c94a", "2 3");

  for (let hour = 0; hour < hours.length; hour += 12) {
    root.append(svg("line", { x1: x(hour), y1: height - padBottom, x2: x(hour), y2: height - padBottom + 4, stroke: "#333a2d" }));
    const label = svg("text", { x: x(hour), y: height - 6, fill: "#97a08c", "font-size": "10", "text-anchor": "middle" });
    label.textContent = `H+${hour}`;
    root.append(label);
  }
  [0, maxKw / 2, maxKw].forEach((kw) => {
    const label = svg("text", { x: 4, y: y(kw) + 3, fill: "#97a08c", "font-size": "10" });
    label.textContent = `${Math.round(kw)} kW`;
    root.append(label);
  });
  const legend = [["critical demand", "#c9503f"], ["secondary demand", "#97a08c"], ["solar (relative)", "#b8c94a"], ["grid available", "#2b3524"]];
  legend.forEach(([text, colour], i) => {
    root.append(svg("rect", { x: padLeft + i * 150, y: 0, width: 10, height: 8, fill: colour }));
    const label = svg("text", { x: padLeft + i * 150 + 14, y: 8, fill: "#97a08c", "font-size": "10" });
    label.textContent = text;
    root.append(label);
  });
  return root;
}

function timelineChart(steps) {
  const width = 900, height = 220, padLeft = 42, padBottom = 22, padTop = 12;
  if (!steps.length) return el("p", { class: "note" }, "No projection available.");
  const sources = [
    ["grid_kw", "#4d7ea8"],
    ["pv_kw", "#b8c94a"],
    ["generator_total_kw", "#d98b2b"],
    ["battery_discharge_kw", "#7fae5e"],
  ];
  const totals = steps.map((s) => sources.reduce((sum, [key]) => sum + (s[key] || 0), 0));
  const maxKw = Math.max(...totals, ...steps.map((s) => s.critical_demand_kw)) * 1.15 || 1;
  const bandWidth = (width - padLeft - 10) / steps.length;
  const x = (i) => padLeft + i * bandWidth;
  const y = (kw) => padTop + (1 - kw / maxKw) * (height - padTop - padBottom);
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });

  steps.forEach((step, i) => {
    let base = 0;
    for (const [key, colour] of sources) {
      const value = step[key] || 0;
      if (value <= 0) continue;
      root.append(svg("rect", {
        x: x(i) + 0.5, y: y(base + value), width: Math.max(1, bandWidth - 1),
        height: Math.max(0, y(base) - y(base + value)), fill: colour,
      }));
      base += value;
    }
    if (step.unserved_critical_kw > 0.01) {
      root.append(svg("rect", { x: x(i) + 0.5, y: padTop, width: Math.max(1, bandWidth - 1), height: height - padTop - padBottom, fill: "#c9503f", opacity: "0.35" }));
    }
  });
  const d = steps.map((s, i) => `${i ? "L" : "M"}${(x(i) + bandWidth / 2).toFixed(1)},${y(s.critical_demand_kw).toFixed(1)}`).join(" ");
  root.append(svg("path", { d, fill: "none", stroke: "#dfe3d8", "stroke-width": "2" }));

  steps.forEach((step, i) => {
    if (i % 12) return;
    const label = svg("text", { x: x(i), y: height - 6, fill: "#97a08c", "font-size": "10", "text-anchor": "middle" });
    label.textContent = `H+${h0(step.hour)}`;
    root.append(label);
  });
  [0, maxKw / 2, maxKw].forEach((kw) => {
    const label = svg("text", { x: 4, y: y(kw) + 3, fill: "#97a08c", "font-size": "10" });
    label.textContent = `${Math.round(kw)} kW`;
    root.append(label);
  });
  const legend = [["grid", "#4d7ea8"], ["PV", "#b8c94a"], ["generator", "#d98b2b"], ["battery", "#7fae5e"], ["critical demand", "#dfe3d8"]];
  legend.forEach(([text, colour], i) => {
    root.append(svg("rect", { x: padLeft + i * 130, y: 0, width: 10, height: 8, fill: colour }));
    const label = svg("text", { x: padLeft + i * 130 + 14, y: 8, fill: "#97a08c", "font-size": "10" });
    label.textContent = text;
    root.append(label);
  });
  return root;
}

/* ------------------------------------------------------------ configure */

function metricTile(key, value) {
  return el("div", { class: "metric" }, el("span", { class: "k" }, key), el("span", { class: "v" }, value));
}

function optionCard(option, recommendedId) {
  const metrics = option.metrics;
  const isRecommended = option.configuration.configuration_id === recommendedId;
  const card = el("div", { class: `panel option${isRecommended ? " recommended" : ""}` },
    el("h3", {}, option.configuration.label,
      isRecommended ? el("span", { class: "badge yes" }, " RECOMMENDED ") : null,
      el("span", { class: `badge ${option.feasible ? "yes" : "no"}` }, option.feasible ? " FEASIBLE " : " NOT FEASIBLE ")),
    el("p", { class: "intent" }, option.intent),
    el("ul", {}, option.configuration.description.map((line) => el("li", {}, line))),
    el("div", { class: "metrics" },
      metricTile("ENDURANCE", `${h0(metrics.ENDURANCE_HOURS)} h`),
      metricTile("CRITICAL COVERAGE", pct(metrics.CRITICAL_LOAD_COVERAGE)),
      metricTile("SECONDARY COVERAGE", pct(metrics.SECONDARY_LOAD_COVERAGE)),
      metricTile("FUEL USED", `${h0(metrics.FUEL_CONSUMPTION)} L`),
      metricTile("MIN RESERVE", `${h1(metrics.energy_reserve_hours_min)} h`),
      metricTile("RESERVE WITHHELD", `${h0(metrics.ENERGY_RESERVE_WITHHELD)} kWh`),
      metricTile("GRID DEPENDENCE", pct(metrics.GRID_DEPENDENCE)),
      metricTile("ACTIVE ASSETS", metrics.NUMBER_OF_ACTIVE_ASSETS),
      metricTile("SPOF", metrics.single_point_of_failure_count),
      metricTile("RECOVERY OPTIONS", metrics.recovery_option_count),
      metricTile("SETUP PATH", `${h0(metrics.DEPLOYMENT_COMPLEXITY.setup_critical_path_min)} min`)),
    option.caveats.length
      ? el("ul", {}, option.caveats.map((c) => el("li", { class: "caveat" }, c)))
      : null,
    metrics.SINGLE_POINTS_OF_FAILURE.length
      ? el("p", { class: "note" }, `Single points of failure: ${metrics.SINGLE_POINTS_OF_FAILURE.map((s) => s.asset_id).join(", ")}`)
      : null,
    option.preauthorised_degradations.length
      ? el("p", { class: "caveat" }, `Relies on the degraded mode the operator pre-authorised for ${option.preauthorised_degradations.join(", ")}. Confirm that authorisation still stands.`)
      : null,
    metrics.unhonoured_priorities.length
      ? el("p", { class: "caveat" }, `Does not serve ${metrics.unhonoured_priorities.join(", ")}, which the operator asked for whenever affordable.`)
      : null,
    metrics.ENERGY_RESERVE_WITHHELD > 0.5
      ? el("p", { class: "note" }, `Withheld from the reserve: ${h0(metrics.ENERGY_RESERVE_WITHHELD)} kWh the node holds but this configuration cannot reach.`)
      : null,
    metrics.OPEN_QUESTIONS.length
      ? el("ul", {}, metrics.OPEN_QUESTIONS.map((q) => el("li", { class: "caveat" }, `${q.question} ${q.impact}`)))
      : null,
    el("div", { class: "actions" },
      el("button", {
        onclick: async () => {
          const rationale = prompt("Operator rationale for this decision (recorded):", isRecommended ? "Accepts the recommended option." : "Operator override.");
          if (rationale === null) return;
          STATE.operate = await api.post("/api/select", {
            configuration_id: option.configuration.configuration_id, rationale,
          });
          renderOperate(STATE.operate);
          showView("operate");
        },
      }, "SELECT THIS CONFIGURATION")));
  return card;
}

function renderRecommendation(recommendation) {
  const node = clear($("recommendation"));
  if (!recommendation) return;
  const confidence = recommendation.confidence;
  node.append(el("div", { class: "panel option recommended" },
    el("h2", {}, "RECOMMENDED OPTION"),
    el("h3", {}, recommendation.recommended_label),
    el("h2", {}, "WHY"),
    el("ul", {}, recommendation.why.map((w) => el("li", {}, w))),
    recommendation.trade_offs.length ? el("h2", {}, "TRADE-OFFS") : null,
    recommendation.trade_offs.length
      ? el("ul", {}, recommendation.trade_offs.map((t) => el("li", {}, t.statement))) : null,
    confidence ? el("h2", {}, `CONFIDENCE — ${confidence.level} (${confidence.critical_assurance_holds_in})`) : null,
    confidence ? el("p", { class: "note" }, confidence.basis) : null,
    confidence ? el("ul", {}, confidence.variants.map((v) => el("li", {},
      el("span", { class: `badge ${v.critical_assurance_holds ? "yes" : "no"}` },
        v.critical_assurance_holds ? " HOLDS " : " FAILS "),
      ` ${v.description} — endurance ${h0(v.endurance_hours)} h`))) : null,
    recommendation.caveats.length ? el("h2", {}, "CAVEATS") : null,
    recommendation.caveats.length
      ? el("ul", {}, recommendation.caveats.map((c) => el("li", { class: "caveat" }, c))) : null,
    el("p", { class: "note" }, recommendation.decision_prompt)));
}

function renderPlan(payload) {
  STATE.plan = payload.plan;
  STATE.recommendation = payload.recommendation;
  STATE.comparison = payload.comparison;
  const recommendedId = payload.recommendation ? payload.recommendation.recommended_configuration_id : "";

  renderRecommendation(payload.recommendation);
  const options = clear($("options"));
  for (const option of payload.plan.options) options.append(optionCard(option, recommendedId));

  $("configure-status").textContent =
    `${payload.plan.candidates_evaluated} candidates evaluated, ${payload.plan.feasible_candidates} feasible.`;
  $("flow-options").replaceChildren(
    el("strong", {}, "OPTIONS"),
    el("span", {}, payload.plan.options.map((o) => o.configuration.label).join("   ·   ")));

  const comparison = clear($("comparison"));
  const table = el("table", {});
  table.append(el("tr", {}, el("th", {}, "Metric"),
    payload.plan.options.map((o) => el("th", { class: "num" }, o.configuration.configuration_id))));
  for (const row of payload.comparison) {
    table.append(el("tr", {},
      el("td", {}, `${row.title}${row.unit && row.unit !== "%" ? ` [${row.unit}]` : ""}`),
      row.values.map((v) => el("td", { class: `num${v.is_best ? " best" : ""}` },
        row.unit === "%" ? pct(v.value) : (Number.isInteger(v.value) ? v.value : h1(v.value))))));
  }
  comparison.append(table);
  comparison.append(el("p", { class: "note" }, "Highlighted = best of the generated options on that dimension alone. Dimensions are shown separately on purpose: there is no composite score."));

  const tradeoffs = clear($("tradeoffs"));
  for (const trade of (payload.recommendation ? payload.recommendation.trade_offs : [])) {
    tradeoffs.append(el("li", {}, trade.statement));
  }
  const discretionary = payload.plan.discretionary_assessment || {};
  $("discretionary").textContent = discretionary.statement || "Not assessed.";
}

/* -------------------------------------------------------------- operate */

function renderAssessment(assessment) {
  const node = clear($("assessment"));
  if (!assessment) return;
  node.append(el("div", { class: "panel" },
    el("h2", {}, `MISSION PICTURE AT H+${h0(assessment.at_hour)}`),
    el("p", {}, el("span", { class: `badge ${assessment.status}` }, ` ${assessment.status} `)),
    el("div", { class: "metrics" },
      metricTile("ASSURED SUPPORT", `${h0(assessment.endurance_remaining_h)} / ${h0(assessment.mission_remaining_h)} h`),
      metricTile("ENERGY RESERVE", `${h1(assessment.reserve_hours)} h`),
      metricTile("RESERVE REQUIRED", `${h0(assessment.reserve_requirement_hours)} h`),
      metricTile("FUEL REMAINING", `${h0(assessment.fuel_remaining_l)} L`),
      metricTile("BATTERY", pct(assessment.battery_soc))),
    el("table", {},
      el("tr", {}, el("th", {}, "Critical supported"), el("td", {}, assessment.critical_functions_supported.join(", ") || "none")),
      el("tr", {}, el("th", {}, "Critical degraded"), el("td", {}, assessment.functions_degraded.join(", ") || "none")),
      el("tr", {}, el("th", {}, "Not supported"), el("td", {}, assessment.functions_shed.join(", ") || "none")),
      el("tr", {}, el("th", {}, "Unavailable assets"), el("td", {}, assessment.unavailable_assets.join(", ") || "none"))),
    assessment.alerts.length ? el("h2", {}, "ALERTS") : null,
    assessment.alerts.length ? el("ul", {}, assessment.alerts.map((a) => el("li", { class: "alert" }, a))) : null));
}

function renderPremises(report) {
  const node = clear($("premises"));
  if (!report) return;
  // Contradicted, but crossing no line the mission states: shown quietly rather
  // than raised. RQ-017 measured what raising these costs the panel's credit.
  for (const consequence of (report.noted || [])) {
    const breach = consequence.breach;
    node.append(el("div", { class: "panel" },
      el("p", { class: "note" },
        el("strong", {}, `NOTED — ${breach.premise.key}. `),
        breach.evidence[0] + " " + consequence.matters_because[consequence.matters_because.length - 1])));
  }
  if (report.clear) return;
  for (const consequence of (report.raised || report.consequences)) {
    const breach = consequence.breach;
    node.append(el("div", { class: "panel option recommended" },
      el("h2", {}, `PREMISE CONTRADICTED — ${breach.premise.key}`),
      el("p", {}, el("strong", {}, "The mission says: "), breach.premise.statement,
        el("span", { class: "tag" }, `${breach.premise.source}${breach.premise.assumption_id ? " · " + breach.premise.assumption_id : ""}`)),
      el("h2", {}, "OBSERVED"),
      el("ul", {}, breach.evidence.map((line) => el("li", { class: "alert" }, line))),
      el("h2", {}, "WHY IT MATTERS"),
      el("ul", {}, consequence.matters_because.map((line) => el("li", {}, line))),
      el("h2", {}, "REVISION OFFERED"),
      el("p", {}, breach.revision_statement),
      breach.conservative
        ? el("p", { class: "note" }, "Conservative on purpose: supply that has not appeared when it was due is not assumed to appear later.")
        : null,
      el("p", { class: "note" }, report.decision_prompt),
      el("div", { class: "actions" },
        el("button", {
          class: "warn",
          onclick: async () => {
            const rationale = prompt("Operator rationale for accepting this revised premise (recorded):", breach.revision_statement);
            if (rationale === null) return;
            const payload = await api.post("/api/accept-premise", { key: breach.premise.key, rationale });
            renderOperate(payload);
            clear($("options")); clear($("recommendation"));
            $("configure-status").textContent = "Premise revised — press GENERATE CONFIGURATIONS to replan.";
          },
        }, "ACCEPT THIS REVISION AND REPLAN"))));
  }
}

function renderReport(report) {
  const node = clear($("report"));
  if (!report) return;
  node.append(el("div", { class: "panel" },
    el("h2", {}, "WHAT CHANGED"),
    el("ul", {}, report.what_changed.map((line) => el("li", {}, line))),
    el("h2", {}, "WHY IT MATTERS"),
    el("ul", {}, report.why_it_matters.map((line) => el("li", {}, line))),
    el("h2", {}, "AVAILABLE ACTIONS ON THE CURRENT CONFIGURATION"),
    report.recovery_options.length
      ? el("table", {},
          el("tr", {}, el("th", {}, "Action"), el("th", { class: "num" }, "Endurance"),
            el("th", { class: "num" }, "Fuel"), el("th", { class: "num" }, "Reserve"),
            el("th", { class: "num" }, "Ready in"), el("th", {}, "Cost")),
          report.recovery_options.map((option) => el("tr", {},
            el("td", {}, option.action, option.restores_critical_assurance
              ? el("span", { class: "badge yes" }, " RESTORES ASSURANCE ") : null),
            el("td", { class: "num" }, `${option.endurance_delta_h >= 0 ? "+" : ""}${h1(option.endurance_delta_h)} h`),
            el("td", { class: "num" }, `${option.fuel_delta_l >= 0 ? "+" : ""}${h0(option.fuel_delta_l)} L`),
            el("td", { class: "num" }, `${option.reserve_delta_h >= 0 ? "+" : ""}${h1(option.reserve_delta_h)} h`),
            el("td", { class: "num" }, `${h0(option.time_to_effect_min)} min`),
            el("td", {}, option.cost_note))))
      : el("p", { class: "note" }, "No further actions available on this configuration."),
    el("p", { class: "note" }, report.decision_prompt)));
  if (report.options) {
    node.append(el("p", { class: "note" }, "New configurations have been generated. Review them on CONFIGURE and COMPARE."));
  }
}

function renderOperate(payload) {
  STATE.operate = payload;
  if (!payload || !payload.selected) {
    $("operate-config").textContent = "No configuration selected. Choose one on CONFIGURE.";
    return;
  }
  $("operate-config").textContent =
    `Running ${payload.selected.configuration.label} [${payload.selected.configuration.configuration_id}]`;
  renderAssessment(payload.assessment);
  renderPremises(payload.premises);
  $("timeline-chart").replaceChildren(timelineChart(payload.timeline || []));
  renderReport(payload.report);

  const decisions = clear($("decisions"));
  decisions.append(el("tr", {}, el("th", {}, "At"), el("th", {}, "Decision"),
    el("th", {}, "Configuration"), el("th", {}, "Followed recommendation"), el("th", {}, "Rationale")));
  for (const decision of payload.status.decisions) {
    decisions.append(el("tr", {},
      el("td", {}, `H+${h0(decision.at_hour)}`),
      el("td", {}, decision.decision),
      el("td", {}, decision.configuration_id),
      el("td", {}, decision.followed_recommendation ? "yes" : "NO — operator override"),
      el("td", {}, decision.rationale || "—")));
  }
}

/* --------------------------------------------------------------- wiring */

async function busy(button, label, work) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try { return await work(); } finally { button.disabled = false; button.textContent = original; }
}

$("btn-generate").addEventListener("click", (event) => busy(event.target, "PLANNING…", async () => {
  const strategies = ["MAX_ENDURANCE", "MIN_FUEL", "MIN_LOGISTICS"];
  if ($("opt-secondary").checked) strategies.push("MAX_SUPPORTED_FUNCTIONS");
  renderPlan(await api.post("/api/configure", { strategies }));
}));

$("btn-advance").addEventListener("click", (event) => busy(event.target, "RUNNING…", async () => {
  renderOperate(await api.post("/api/advance", { hour: Number($("advance-hour").value) }));
}));

$("btn-degrade").addEventListener("click", (event) => busy(event.target, "RE-PLANNING…", async () => {
  const payload = await api.post("/api/degrade", { scenario_id: $("scenario-select").value });
  renderOperate(payload);
  if (payload.report && payload.report.options) {
    renderPlan({
      plan: payload.report.options,
      comparison: payload.report.comparison,
      recommendation: payload.report.recommendation,
    });
  }
}));

$("btn-reset").addEventListener("click", (event) => busy(event.target, "RESETTING…", async () => {
  await api.post("/api/reset", { world: $("world-select").value });
  clear($("options")); clear($("recommendation")); clear($("report")); clear($("assessment"));
  $("configure-status").textContent = "";
  renderOperate(await api.get("/api/operate"));
  renderMission(await api.get("/api/mission"));
  showView("mission");
}));

(async function boot() {
  renderMission(await api.get("/api/mission"));
  const plan = await api.get("/api/plan");
  if (plan.plan) renderPlan(plan);
  renderOperate(await api.get("/api/operate"));
})();
