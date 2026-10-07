import { getAnalytics, requireAuth } from "./api.js";
import { displayValue, element, humanize, mountAuthControls } from "./common.js";

const filterForm = document.querySelector("#analytics-filters");
const status = document.querySelector("#analytics-status");
const kpis = document.querySelector("#analytics-kpis");
const errorMessage = document.querySelector("#analytics-error");
const totalValue = document.querySelector("#total-enquiry-value");
const chartTargets = {
  customerStages: document.querySelector("#chart-customer-stages"),
  enquiryStatus: document.querySelector("#chart-enquiry-status"),
  enquiryPriority: document.querySelector("#chart-enquiry-priority"),
  pipeline: document.querySelector("#chart-pipeline"),
  activity: document.querySelector("#chart-activity"),
  followups: document.querySelector("#chart-followups"),
};

function numberValue(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

function renderBars(target, records, valueKey = "count") {
  target.replaceChildren();
  const values = Array.isArray(records)
    ? records.filter((record) => record && record.label !== undefined)
    : [];
  if (values.length === 0) {
    target.append(element("p", "chart-empty", "No data available for this period."));
    return;
  }
  const max = Math.max(...values.map((item) => numberValue(item[valueKey])), 0);
  const list = element("ul", "chart-bars");
  for (const item of values) {
    const row = element("li", "chart-bar-row");
    const label = element("span", "chart-label", humanize(item.label));
    const track = element("span", "chart-track");
    const bar = element("span", "chart-bar");
    const value = numberValue(item[valueKey]);
    bar.style.width = `${max > 0 ? (value / max) * 100 : 0}%`;
    track.append(bar);
    row.append(label, track, element("strong", "chart-value", displayValue(item[valueKey], "0")));
    list.append(row);
  }
  target.append(list);
}

function renderActivity(target, records) {
  target.replaceChildren();
  const values = Array.isArray(records) ? records : [];
  if (values.length === 0) {
    target.append(element("p", "chart-empty", "No data available for this period."));
    return;
  }
  const max = Math.max(
    ...values.map((item) => numberValue(item.meetings) + numberValue(item.calls) + numberValue(item.followups)),
    0,
  );
  const list = element("ul", "activity-bars");
  for (const item of values) {
    const total = numberValue(item.meetings) + numberValue(item.calls) + numberValue(item.followups);
    const row = element("li", "activity-bar-row");
    row.append(element("span", "chart-label", displayValue(item.period)));
    const segments = element("span", "activity-segments");
    for (const [kind, className] of [
      ["meetings", "segment-meetings"],
      ["calls", "segment-calls"],
      ["followups", "segment-followups"],
    ]) {
      const count = numberValue(item[kind]);
      const segment = element("span", `activity-segment ${className}`);
      segment.style.width = `${max > 0 ? (count / max) * 100 : 0}%`;
      segment.title = `${humanize(kind)}: ${count}`;
      segments.append(segment);
    }
    row.append(segments, element("strong", "chart-value", String(total)));
    list.append(row);
  }
  target.append(list);
}

function setKpis(overview) {
  for (const [name, value] of Object.entries(overview ?? {})) {
    const target = kpis.querySelector(`[data-metric="${name}"]`);
    if (target) target.textContent = String(numberValue(value));
  }
  kpis.hidden = false;
}

function queryParams() {
  const formData = new FormData(filterForm);
  return {
    start_date: formData.get("start_date"),
    end_date: formData.get("end_date"),
    grain: formData.get("grain") || "monthly",
  };
}

async function loadAnalytics() {
  if (!requireAuth()) return;
  const params = queryParams();
  status.hidden = false;
  status.textContent = "Loading analytics...";
  errorMessage.hidden = true;
  kpis.hidden = true;
  for (const target of Object.values(chartTargets)) target.replaceChildren();
  try {
    const [overview, customers, enquiries, activities, pipeline, followups] = await Promise.all([
      getAnalytics("overview", params),
      getAnalytics("customers", params),
      getAnalytics("enquiries", params),
      getAnalytics("activities", params),
      getAnalytics("pipeline", params),
      getAnalytics("followups", params),
    ]);
    setKpis(overview);
    renderBars(chartTargets.customerStages, customers.by_sales_stage);
    renderBars(chartTargets.enquiryStatus, enquiries.by_status);
    renderBars(chartTargets.enquiryPriority, enquiries.by_priority);
    renderBars(chartTargets.pipeline, pipeline.by_sales_stage, "value");
    renderActivity(chartTargets.activity, activities.over_time);
    renderBars(chartTargets.followups, followups.by_status);
    totalValue.textContent = displayValue(enquiries.total_estimated_value, "0");
    document.querySelector("#followup-due-summary").textContent =
      `Due today: ${numberValue(followups.due_today)} · Due in the next 7 days: ${numberValue(followups.due_next_7_days)}`;
    status.hidden = true;
  } catch {
    status.hidden = true;
    kpis.hidden = true;
    errorMessage.hidden = false;
    errorMessage.textContent = "Unable to load analytics.";
  }
}

filterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!requireAuth()) return;
  const params = queryParams();
  if (params.start_date && params.end_date && params.start_date > params.end_date) {
    errorMessage.hidden = false;
    errorMessage.textContent = "Start date must be on or before end date.";
    return;
  }
  void loadAnalytics();
});

mountAuthControls();
if (requireAuth()) {
  void loadAnalytics();
}

