"use strict";

const modeButtons = document.querySelectorAll("[data-mode]");
const sourcePanels = document.querySelectorAll("[data-panel]");

for (const button of modeButtons) {
  button.addEventListener("click", () => {
    const selectedMode = button.dataset.mode;

    for (const candidate of modeButtons) {
      const isSelected = candidate === button;
      candidate.classList.toggle("is-active", isSelected);
      candidate.setAttribute("aria-pressed", String(isSelected));
    }

    for (const panel of sourcePanels) {
      panel.hidden = panel.dataset.panel !== selectedMode;
    }
  });
}
