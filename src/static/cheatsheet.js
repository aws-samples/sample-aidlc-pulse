(function () {
  var root = document.documentElement;
  var saved = localStorage.getItem('aws-ds-theme');
  if (saved === 'dark' || saved === 'light') root.setAttribute('data-theme', saved);
  document.getElementById('theme').addEventListener('click', function () {
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem('aws-ds-theme', next);
  });

  var toast = document.getElementById('toast');
  var toastTimer = null;
  function showToast(msg) {
    toast.textContent = msg;
    toast.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove('on'); }, 1200);
  }
  document.getElementById('grid').addEventListener('click', function (e) {
    var el = e.target.closest('code');
    if (!el || el.closest('.inline')) return;
    var text = el.textContent.replace(/^\d\.\d\s+/, '');
    navigator.clipboard.writeText(text).then(function () {
      el.classList.add('copied');
      setTimeout(function () { el.classList.remove('copied'); }, 700);
      showToast('Copied: ' + text);
    }, function () { showToast('Copy failed'); });
  });

  var q = document.getElementById('q');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  q.addEventListener('input', function () {
    var term = q.value.trim().toLowerCase();
    cards.forEach(function (card) {
      var titleHit = card.querySelector('h2').textContent.toLowerCase().indexOf(term) !== -1;
      var rows = card.querySelectorAll('tr, .chips code');
      var any = false;
      rows.forEach(function (r) {
        if (r.querySelector('th')) return;
        var hit = !term || titleHit || r.textContent.toLowerCase().indexOf(term) !== -1;
        r.classList.toggle('hide', !hit);
        if (hit) any = true;
      });
      card.classList.toggle('hide', term !== '' && !any && !titleHit);
    });
  });
})();
