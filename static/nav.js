document.addEventListener("DOMContentLoaded", function () {
  const menuBtn = document.querySelector(".menu-toggle");
  const sidebar = document.querySelector(".sidebar");
  const mobileVisitorCards = document.querySelectorAll(".visitor-mobile-card[data-detail-url]");

  if (menuBtn && sidebar) {
    menuBtn.addEventListener("click", function () {
      sidebar.classList.toggle("active");
    });
  }

  // Close menu on link click (mobile only)
  document.querySelectorAll(".sidebar a").forEach(link => {
    link.addEventListener("click", function () {
      sidebar.classList.remove("active");
    });
  });

  mobileVisitorCards.forEach(card => {
    const openCard = function () {
      window.location.href = card.dataset.detailUrl;
    };

    card.addEventListener("click", function (event) {
      if (event.target.closest("a, button, input, select, textarea, label, form")) {
        return;
      }
      openCard();
    });

    card.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openCard();
      }
    });
  });
});
