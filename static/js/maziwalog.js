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
