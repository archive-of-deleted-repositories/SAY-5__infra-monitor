(function () {
  "use strict";

  const METRICS = [
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "network_bytes_recv",
  ];

  const PLOTLY_LAYOUT = {
    paper_bgcolor: "#161b22",
    plot_bgcolor: "#161b22",
    font: { color: "#c9d1d9", size: 11 },
    margin: { l: 40, r: 10, t: 30, b: 30 },
    xaxis: { gridcolor: "#30363d", color: "#8b949e" },
    yaxis: { gridcolor: "#30363d", color: "#8b949e" },
  };

  let currentHostId = null;
  let currentHostName = null;
  let currentMinutes = 60;

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".host-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".host-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentHostId = parseInt(btn.dataset.hostId, 10);
        currentHostName = btn.dataset.hostName;
        document.getElementById("selected-host").textContent = currentHostName;
        refreshCharts();
        document.getElementById("sidebar").classList.remove("open");
      });
    });

    document.querySelectorAll(".range").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".range").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentMinutes = parseInt(btn.dataset.minutes, 10);
        refreshCharts();
      });
    });

    const toggle = document.getElementById("drawer-toggle");
    if (toggle) {
      toggle.addEventListener("click", () => {
        document.getElementById("sidebar").classList.toggle("open");
      });
    }

    refreshAlerts();
    refreshRemediations();
    setInterval(refreshAlerts, 10_000);
    setInterval(refreshRemediations, 10_000);
    setInterval(refreshCharts, 10_000);
  });

  async function refreshCharts() {
    if (currentHostId === null) return;
    for (const metric of METRICS) {
      try {
        const r = await fetch(
          `/api/metrics?host_id=${currentHostId}&metric=${metric}&minutes=${currentMinutes}`
        );
        const data = await r.json();
        const trace = {
          x: data.points.map((p) => p.t),
          y: data.points.map((p) => p.v),
          mode: "lines",
          line: { color: "#58a6ff", width: 1.5 },
          name: metric,
        };
        const layout = Object.assign({ title: metric }, PLOTLY_LAYOUT);
        Plotly.react(`chart-${metric}`, [trace], layout, { displayModeBar: false });
      } catch (err) {
        // network blip; skip this round
      }
    }
  }

  async function refreshAlerts() {
    try {
      const r = await fetch("/api/alerts?limit=20");
      const rows = await r.json();
      const tbody = document.querySelector("#alerts-table tbody");
      tbody.innerHTML = rows
        .map(
          (a) =>
            `<tr><td>${a.fired_at}</td><td>${a.rule_id}</td><td>${a.host_id}</td><td>${a.value.toFixed(
              2
            )}</td><td>${a.severity}</td></tr>`
        )
        .join("");
    } catch (err) {
      // ignore
    }
  }

  async function refreshRemediations() {
    try {
      const r = await fetch("/api/remediations?limit=20");
      const rows = await r.json();
      const tbody = document.querySelector("#remediations-table tbody");
      tbody.innerHTML = rows
        .map(
          (a) =>
            `<tr><td>${a.fired_at}</td><td><code>${escape(a.script)}</code></td><td>${escape(
              JSON.stringify(a.args)
            )}</td><td>${a.exit_code === null ? "—" : a.exit_code}</td><td>${a.duration_ms}</td></tr>`
        )
        .join("");
    } catch (err) {
      // ignore
    }
  }

  function escape(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
    })[c]);
  }
})();
