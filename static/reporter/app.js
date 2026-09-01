document.addEventListener("DOMContentLoaded", () => {
  const selectAll = document.querySelector("[data-check-all]");
  if (selectAll) {
    selectAll.addEventListener("change", () => {
      document.querySelectorAll('input[name="dashboard_ids"]').forEach((box) => {
        box.checked = selectAll.checked;
      });
    });
  }

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  const runForm = document.querySelector("[data-run-form]");
  if (runForm) {
    const allDashboards = runForm.querySelector("[data-run-all]");
    const selectedDashboards = runForm.querySelector("[data-run-dashboards]");
    if (allDashboards && selectedDashboards) {
      selectedDashboards.addEventListener("change", () => {
        if (Array.from(selectedDashboards.options).some((option) => option.selected)) {
          allDashboards.checked = false;
        }
      });
      allDashboards.addEventListener("change", () => {
        if (allDashboards.checked) {
          Array.from(selectedDashboards.options).forEach((option) => {
            option.selected = false;
          });
        }
      });
    }
  }

  const summary = document.querySelector("[data-run-status-url]");
  if (summary && summary.dataset.running === "true") {
    const poll = async () => {
      try {
        const response = await fetch(summary.dataset.runStatusUrl, {headers: {"Accept": "application/json"}});
        if (!response.ok) return;
        const data = await response.json();
        summary.querySelector("[data-status]").textContent = data.status_label;
        summary.querySelector("[data-progress-label]").textContent = `${data.completed + data.failed}/${data.total}`;
        summary.querySelector("[data-progress-bar]").style.width = `${data.progress}%`;
        summary.querySelector("[data-summary]").textContent = data.summary;
        if (data.finished) window.location.reload();
      } catch (_) {
        // The next poll will recover from a short network interruption.
      }
    };
    window.setInterval(poll, 3000);
  }
});
