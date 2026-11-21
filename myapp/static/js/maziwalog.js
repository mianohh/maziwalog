(function () {
  'use strict';

  function setSidebar(open) {
    var sidebar = document.getElementById('mlSidebar');
    var scrim = document.getElementById('mlSidebarScrim');
    var toggle = document.getElementById('mlSidebarToggle');
    if (!sidebar) return;
    sidebar.classList.toggle('ml-open', open);
    if (scrim) scrim.classList.toggle('ml-open', open);
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    document.body.style.overflow = open ? 'hidden' : '';
  }

  document.addEventListener('DOMContentLoaded', function () {
    var toggle = document.getElementById('mlSidebarToggle');
    var scrim = document.getElementById('mlSidebarScrim');
    if (toggle) {
      toggle.addEventListener('click', function () {
        var sidebar = document.getElementById('mlSidebar');
        setSidebar(!(sidebar && sidebar.classList.contains('ml-open')));
      });
    }
    if (scrim) scrim.addEventListener('click', function () { setSidebar(false); });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') setSidebar(false);
    });
  });
})();

(function () {
  'use strict';

  // Auto-dismiss flash messages: visible briefly, then faded out.
  // Hovering an alert pauses the countdown so longer text stays readable.
  function closeAlert(el) {
    if (window.bootstrap && window.bootstrap.Alert) {
      window.bootstrap.Alert.getOrCreateInstance(el).close();
    } else {
      el.classList.remove('show');
      window.setTimeout(function () { el.remove(); }, 200);
    }
  }

  function scheduleDismiss(el) {
    var ms = parseInt(el.getAttribute('data-ml-autodismiss'), 10);
    if (!ms || ms <= 0) return;
    var timer = window.setTimeout(function () { closeAlert(el); }, ms);
    el.addEventListener('mouseenter', function () { window.clearTimeout(timer); });
    el.addEventListener('mouseleave', function () {
      timer = window.setTimeout(function () { closeAlert(el); }, ms);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-ml-autodismiss]').forEach(scheduleDismiss);
  });
})();
