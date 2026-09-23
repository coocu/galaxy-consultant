/* 번호표 영역만 화면 아래 여백 안으로 제한. 나머지 카드/호출/관리자 배치는 그대로. */
(() => {
  let scheduled = false;
  function fit() {
    scheduled = false;
    const viewportHeight = window.visualViewport?.height || window.innerHeight;
    document.querySelectorAll(".viewer-service-card .queue-list").forEach((queue) => {
      const card = queue.closest(".viewer-service-card");
      const chip = queue.querySelector(".queue-chip");
      if (!chip) return;
      const gap = parseFloat(getComputedStyle(queue).rowGap) || 0;
      const rowHeight = chip.getBoundingClientRect().height;
      const padding = parseFloat(getComputedStyle(card).paddingBottom) || 0;
      const available = viewportHeight - queue.getBoundingClientRect().top - padding - 16;
      // 한 행보다 공간이 적은 초소형 화면에서는 페이지 스크롤을 허용한다.
      // 일반 모니터에서는 완전한 행 단위 높이로 잘라 마지막 카드가 반만 보이지 않는다.
      const rows = Math.max(1, Math.floor((available + gap) / (rowHeight + gap)));
      const height = Math.max(rowHeight, rows * (rowHeight + gap) - gap);
      queue.style.setProperty("--queue-viewport-height", `${height}px`);
    });
  }
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(fit);
  }
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("app");
    if (root) new MutationObserver(schedule).observe(root, { childList: true, subtree: true });
    schedule();
  });
  window.addEventListener("resize", schedule);
  window.visualViewport?.addEventListener("resize", schedule);
})();
