/* The tactical map editor.
 *
 * The rest of this site has no JavaScript on purpose. A map is the one thing
 * that cannot be a form: placing a symbol on terrain and dragging it where it
 * belongs is the feature, and a page of numeric coordinate fields would be
 * worse than the paper map it replaces. So this file exists — vanilla, no
 * build step, no dependency, served from the bot's own container like
 * everything else.
 *
 * What it is NOT is a second renderer. Every frame, icon and arrowhead is
 * defined once by `utils/tacmap.py` and emitted into the page's <defs>; the
 * composition below only places <use> elements at coordinates, and mirrors
 * `item_svg()` in that module element for element. If one side changes, the
 * other has to change with it — the map drawn here and the map rendered for
 * somebody with scripting off are meant to be the same map.
 *
 * With scripting off the server-rendered SVG is still there and still readable.
 * That is the fallback, and it is why this file replaces the drawing layer
 * rather than building the whole map from nothing.
 */
(function () {
  'use strict';

  var app = document.getElementById('tmapp');
  if (!app) return;

  var svg = app.querySelector('svg.tacmap');
  var docNode = document.getElementById('tmdoc');
  var catalogNode = document.getElementById('tmcatalog');
  if (!svg || !docNode || !catalogNode) return;

  var catalog = JSON.parse(catalogNode.textContent);
  var state = {
    doc: JSON.parse(docNode.textContent),
    tool: 'select',
    side: 'friend',
    symbol: 'inf',
    glyph: catalog.points.length ? catalog.points[0].glyph : 'OBJ',
    hq: false,
    selected: -1,
    draft: null,
    drag: null,
    pan: null,
    undo: [],
    dirty: false
  };
  var editable = app.dataset.editable === '1';

  var colours = {};
  catalog.sides.forEach(function (side) { colours[side.key] = side; });
  var symbols = {};
  catalog.symbols.forEach(function (symbol) { symbols[symbol.key] = symbol; });

  var layer = svg.querySelector('.tm-items');
  var back = svg.querySelector('#tm-back');
  var overlay = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  overlay.setAttribute('class', 'tm-overlay');
  svg.appendChild(overlay);

  var view = readViewBox();

  /* -- drawing, mirroring utils/tacmap.py ---------------------------------- */

  function esc(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function attr(value) { return esc(value).replace(/"/g, '&quot;'); }

  function round(value) { return Math.round(value * 100) / 100; }

  function label(text, x, y, size) {
    if (!text) return '';
    return '<text x="' + round(x) + '" y="' + round(y) + '" text-anchor="middle"' +
      ' font-size="' + round(14 * size) + '" font-weight="600"' +
      ' font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif"' +
      ' fill="#ffffff" stroke="#11161c" stroke-width="' + round(3 * size) + '"' +
      ' paint-order="stroke" stroke-linejoin="round">' + esc(text) + '</text>';
  }

  function unitSVG(item) {
    var paint = colours[item.side];
    var scale = catalog.unitBox * item.size / 100;
    var transform = 'translate(' + round(item.x) + ',' + round(item.y) + ') rotate(' +
      (item.rotation || 0) + ') scale(' + scale + ') translate(-50,-50)';
    var parts = ['<use href="#tmf-' + item.side + '" fill="' + paint.fill +
      '" stroke="' + paint.stroke + '" stroke-width="4"/>'];
    if (item.hq) {
      parts.push('<use href="#tmf-hq" stroke="' + paint.stroke + '" stroke-width="4"/>');
    }
    if ((symbols[item.symbol] || {}).hasIcon) {
      parts.push('<use href="#tmi-' + item.symbol + '" fill="none" stroke="' +
        paint.stroke + '" color="' + paint.stroke + '" stroke-width="7"' +
        ' stroke-linecap="round" stroke-linejoin="round"/>');
    }
    var drop = catalog.unitBox * item.size * (item.hq ? 0.8 : 0.62) + 6;
    return '<g transform="' + transform + '">' + parts.join('') + '</g>' +
      label(item.label, item.x, item.y + drop, item.size);
  }

  function pointSVG(item) {
    var paint = colours[item.side];
    var radius = catalog.pointBox * item.size / 2;
    var parts = ['<circle cx="' + round(item.x) + '" cy="' + round(item.y) + '" r="' +
      round(radius) + '" fill="' + paint.fill + '" stroke="' + paint.stroke +
      '" stroke-width="' + round(3 * item.size) + '"/>'];
    if (item.glyph) {
      parts.push('<text x="' + round(item.x) + '" y="' + round(item.y + radius * 0.36) +
        '" text-anchor="middle" font-size="' + round(radius * 0.95) + '"' +
        ' font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif"' +
        ' font-weight="700" fill="' + paint.stroke + '">' + esc(item.glyph) + '</text>');
    }
    parts.push(label(item.label, item.x, item.y + radius + 16 * item.size, item.size));
    return parts.join('');
  }

  function shapeSVG(item) {
    var paint = colours[item.side];
    var points = item.points.map(function (point) {
      return round(point[0]) + ',' + round(point[1]);
    }).join(' ');
    var width = round(4 * item.size);
    var dash = item.style === 'dashed'
      ? ' stroke-dasharray="' + round(14 * item.size) + ' ' + round(9 * item.size) + '"' : '';
    if (item.kind === 'area') {
      var centre = item.points.reduce(function (sum, point) {
        return [sum[0] + point[0] / item.points.length, sum[1] + point[1] / item.points.length];
      }, [0, 0]);
      return '<polygon points="' + points + '" fill="' + paint.fill +
        '" fill-opacity="0.3" stroke="' + paint.stroke + '" stroke-width="' + width +
        '"' + dash + ' stroke-linejoin="round"/>' +
        label(item.label, centre[0], centre[1], item.size);
    }
    var marker = item.arrow ? ' marker-end="url(#tma-' + item.side + ')"' : '';
    var line = '<polyline points="' + points + '" fill="none" stroke="' + paint.stroke +
      '" stroke-width="' + width + '"' + dash +
      ' stroke-linecap="round" stroke-linejoin="round"' + marker + '/>';
    if (item.label) {
      var middle = item.points[Math.floor(item.points.length / 2)];
      line += label(item.label, middle[0], middle[1] - 10 * item.size, item.size);
    }
    return line;
  }

  function itemSVG(item) {
    if (item.kind === 'unit') return unitSVG(item);
    if (item.kind === 'point') return pointSVG(item);
    if (item.kind === 'text') return label(item.label || ' ', item.x, item.y, item.size * 1.6);
    return shapeSVG(item);
  }

  function hitSVG(item) {
    // A hair-thin line is impossible to grab, so every line and area carries an
    // invisible fat copy of itself for the pointer to land on.
    if (item.kind !== 'line' && item.kind !== 'area') return '';
    var points = item.points.map(function (point) {
      return round(point[0]) + ',' + round(point[1]);
    }).join(' ');
    return '<polyline points="' + points + '" fill="none" stroke="#000"' +
      ' stroke-opacity="0" stroke-width="' + round(20 * item.size) + '"/>';
  }

  function backgroundSVG() {
    var background = state.doc.background || {};
    if (!background.url) {
      return '<rect x="0" y="0" width="' + state.doc.width + '" height="' +
        state.doc.height + '" fill="#20262e"/>';
    }
    return '<image href="' + attr(background.url) + '" x="0" y="0" width="' +
      state.doc.width + '" height="' + state.doc.height + '" opacity="' +
      (background.opacity || 1) + '" preserveAspectRatio="none"/>';
  }

  function gridSVG() {
    var grid = state.doc.grid || {};
    if (!grid.show) return '';
    var lines = [];
    var column;
    for (column = 1; column < grid.cols; column++) {
      var x = round(state.doc.width * column / grid.cols);
      lines.push('<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + state.doc.height + '"/>');
    }
    var row;
    for (row = 1; row < grid.rows; row++) {
      var y = round(state.doc.height * row / grid.rows);
      lines.push('<line x1="0" y1="' + y + '" x2="' + state.doc.width + '" y2="' + y + '"/>');
    }
    return '<g stroke="#ffffff" stroke-opacity="0.28" stroke-width="1.5">' +
      lines.join('') + '</g>';
  }

  function draftSVG() {
    if (!state.draft || state.draft.points.length === 0) return '';
    var paint = colours[state.side];
    var points = state.draft.points.concat(
      state.draft.cursor ? [state.draft.cursor] : []
    ).map(function (point) { return round(point[0]) + ',' + round(point[1]); }).join(' ');
    var tag = state.draft.kind === 'area' ? 'polygon' : 'polyline';
    return '<' + tag + ' points="' + points + '" fill="none" stroke="' + paint.stroke +
      '" stroke-width="4" stroke-dasharray="10 8" stroke-linecap="round"/>';
  }

  function render() {
    layer.innerHTML = state.doc.items.map(function (item, index) {
      return '<g class="tm-item" data-i="' + index + '">' + itemSVG(item) + hitSVG(item) + '</g>';
    }).join('');
    back.innerHTML = backgroundSVG() + gridSVG();
    overlay.innerHTML = draftSVG();
    drawSelection();
  }

  function drawSelection() {
    var existing = overlay.querySelector('.tm-ring');
    if (existing) existing.remove();
    if (state.selected < 0) return;
    var group = layer.querySelector('[data-i="' + state.selected + '"]');
    if (!group) return;
    var box = group.getBBox();
    var ring = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    ring.setAttribute('class', 'tm-ring');
    ring.setAttribute('x', box.x - 6);
    ring.setAttribute('y', box.y - 6);
    ring.setAttribute('width', box.width + 12);
    ring.setAttribute('height', box.height + 12);
    overlay.appendChild(ring);
  }

  /* -- the sheet, zoom and pan --------------------------------------------- */

  function readViewBox() {
    var parts = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    if (parts.length !== 4 || parts.some(isNaN)) {
      return { x: 0, y: 0, w: state.doc.width, h: state.doc.height };
    }
    return { x: parts[0], y: parts[1], w: parts[2], h: parts[3] };
  }

  function applyView() {
    svg.setAttribute('viewBox', view.x + ' ' + view.y + ' ' + view.w + ' ' + view.h);
  }

  function resetView() {
    view = { x: 0, y: 0, w: state.doc.width, h: state.doc.height };
    applyView();
  }

  function at(event) {
    var point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    var matrix = svg.getScreenCTM();
    if (!matrix) return { x: 0, y: 0 };
    return point.matrixTransform(matrix.inverse());
  }

  svg.addEventListener('wheel', function (event) {
    event.preventDefault();
    var pointer = at(event);
    var factor = event.deltaY > 0 ? 1.15 : 1 / 1.15;
    var width = Math.min(Math.max(view.w * factor, state.doc.width / 20), state.doc.width * 3);
    var scale = width / view.w;
    view.x = pointer.x - (pointer.x - view.x) * scale;
    view.y = pointer.y - (pointer.y - view.y) * scale;
    view.w = width;
    view.h = view.h * scale;
    applyView();
  }, { passive: false });

  /* -- everything below is the editor and only runs when it may draw -------- */

  if (!editable) {
    render();
    return;
  }

  var status = document.getElementById('tmstatus');
  var inspector = document.getElementById('tminspector');

  function say(text, kind) {
    status.textContent = text || '';
    status.className = 'tmstatus' + (kind ? ' ' + kind : '');
  }

  function snapshot() {
    state.undo.push(JSON.stringify(state.doc));
    if (state.undo.length > 40) state.undo.shift();
  }

  function touched() {
    state.dirty = true;
    say('Unsaved changes', 'warn');
  }

  function selected() {
    return state.selected >= 0 ? state.doc.items[state.selected] : null;
  }

  function select(index) {
    state.selected = index;
    render();
    fillInspector();
  }

  function add(item) {
    if (state.doc.items.length >= catalog.limits.items) {
      say('That is as much as one map holds (' + catalog.limits.items + ' items).', 'err');
      return;
    }
    snapshot();
    state.doc.items.push(item);
    touched();
    select(state.doc.items.length - 1);
  }

  function newItem(kind, point) {
    var item = { kind: kind, side: state.side, label: '', note: '', size: 1 };
    if (kind === 'unit') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.symbol = state.symbol;
      item.hq = state.hq;
      item.rotation = 0;
    } else if (kind === 'point') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.glyph = state.glyph;
    } else if (kind === 'text') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.label = 'Text';
    }
    return item;
  }

  /* -- pointer handling ----------------------------------------------------- */

  svg.addEventListener('pointerdown', function (event) {
    if (event.button !== 0) return;
    var point = at(event);
    var group = event.target.closest ? event.target.closest('.tm-item') : null;

    if (state.tool === 'line' || state.tool === 'area') {
      if (!state.draft) state.draft = { kind: state.tool, points: [], cursor: null };
      state.draft.points.push([round(point.x), round(point.y)]);
      overlay.innerHTML = draftSVG();
      say(state.draft.points.length + ' points — Enter finishes, Esc cancels');
      return;
    }

    if (state.tool !== 'select') {
      add(newItem(state.tool, point));
      return;
    }

    if (group) {
      var index = Number(group.dataset.i);
      select(index);
      state.drag = { index: index, from: point, moved: false,
                     start: JSON.stringify(state.doc.items[index]) };
      svg.setPointerCapture(event.pointerId);
      return;
    }

    select(-1);
    state.pan = { x: event.clientX, y: event.clientY, view: { x: view.x, y: view.y } };
    svg.setPointerCapture(event.pointerId);
  });

  svg.addEventListener('pointermove', function (event) {
    if (state.draft) {
      var cursor = at(event);
      state.draft.cursor = [round(cursor.x), round(cursor.y)];
      overlay.innerHTML = draftSVG();
      return;
    }
    if (state.drag) {
      var point = at(event);
      var dx = point.x - state.drag.from.x;
      var dy = point.y - state.drag.from.y;
      if (!state.drag.moved && Math.abs(dx) + Math.abs(dy) < 1) return;
      if (!state.drag.moved) {
        state.drag.moved = true;
        snapshot();
      }
      var item = JSON.parse(state.drag.start);
      if (item.points) {
        item.points = item.points.map(function (corner) {
          return [round(corner[0] + dx), round(corner[1] + dy)];
        });
      } else {
        item.x = round(item.x + dx);
        item.y = round(item.y + dy);
      }
      state.doc.items[state.drag.index] = item;
      render();
      return;
    }
    if (state.pan) {
      var scale = view.w / svg.getBoundingClientRect().width;
      view.x = state.pan.view.x - (event.clientX - state.pan.x) * scale;
      view.y = state.pan.view.y - (event.clientY - state.pan.y) * scale;
      applyView();
    }
  });

  svg.addEventListener('pointerup', function () {
    if (state.drag && state.drag.moved) touched();
    state.drag = null;
    state.pan = null;
  });

  svg.addEventListener('dblclick', function (event) {
    if (state.draft) {
      event.preventDefault();
      finishDraft();
    }
  });

  function finishDraft() {
    var draft = state.draft;
    state.draft = null;
    say('');
    if (!draft) return;
    var least = draft.kind === 'area' ? 3 : 2;
    if (draft.points.length < least) {
      overlay.innerHTML = '';
      say('A ' + draft.kind + ' needs at least ' + least + ' points.', 'err');
      return;
    }
    add({
      kind: draft.kind, side: state.side, label: '', note: '', size: 1,
      points: draft.points, style: 'solid',
      arrow: draft.kind === 'line'
    });
  }

  /* -- toolbar -------------------------------------------------------------- */

  function setTool(tool) {
    if (state.draft) finishDraft();
    state.tool = tool;
    Array.prototype.forEach.call(app.querySelectorAll('.tmtool'), function (button) {
      button.classList.toggle('active', button.dataset.tool === tool);
    });
    Array.prototype.forEach.call(app.querySelectorAll('.tmpick [data-for]'), function (field) {
      field.hidden = field.dataset.for !== tool;
    });
    svg.classList.toggle('tm-drawing', tool !== 'select');
  }

  Array.prototype.forEach.call(app.querySelectorAll('.tmtool'), function (button) {
    button.addEventListener('click', function () { setTool(button.dataset.tool); });
  });

  function fillOptions(select, options, value) {
    select.innerHTML = options.map(function (option) {
      return '<option value="' + attr(option.value) + '">' + esc(option.label) + '</option>';
    }).join('');
    select.value = value;
  }

  var sideOptions = catalog.sides.map(function (side) {
    return { value: side.key, label: side.label };
  });
  var symbolOptions = catalog.symbols.map(function (symbol) {
    return { value: symbol.key, label: symbol.group + ' · ' + symbol.label };
  });
  var glyphOptions = catalog.points.map(function (preset) {
    return { value: preset.glyph, label: preset.glyph + ' · ' + preset.label };
  });

  var sidePick = document.getElementById('tmside');
  var symbolPick = document.getElementById('tmsymbol');
  var glyphPick = document.getElementById('tmglyph');
  var hqPick = document.getElementById('tmhq');

  fillOptions(sidePick, sideOptions, state.side);
  fillOptions(symbolPick, symbolOptions, state.symbol);
  fillOptions(glyphPick, glyphOptions, state.glyph);

  sidePick.addEventListener('change', function () { state.side = sidePick.value; });
  symbolPick.addEventListener('change', function () { state.symbol = symbolPick.value; });
  glyphPick.addEventListener('change', function () { state.glyph = glyphPick.value; });
  hqPick.addEventListener('change', function () { state.hq = hqPick.checked; });

  /* -- the inspector -------------------------------------------------------- */

  var fields = {
    label: document.getElementById('tmf-label'),
    note: document.getElementById('tmf-note'),
    side: document.getElementById('tmf-side'),
    symbol: document.getElementById('tmf-symbol'),
    hq: document.getElementById('tmf-hq'),
    glyph: document.getElementById('tmf-glyph'),
    rotation: document.getElementById('tmf-rotation'),
    size: document.getElementById('tmf-size'),
    style: document.getElementById('tmf-style'),
    arrow: document.getElementById('tmf-arrow')
  };
  fillOptions(fields.side, sideOptions, state.side);
  fillOptions(fields.symbol, symbolOptions, state.symbol);

  function fillInspector() {
    var item = selected();
    inspector.hidden = !item;
    if (!item) return;
    Array.prototype.forEach.call(inspector.querySelectorAll('[data-for]'), function (field) {
      field.hidden = field.dataset.for.split(' ').indexOf(item.kind) < 0;
    });
    fields.label.value = item.label || '';
    fields.note.value = item.note || '';
    fields.side.value = item.side;
    fields.symbol.value = item.symbol || 'inf';
    fields.hq.checked = !!item.hq;
    fields.glyph.value = item.glyph || '';
    fields.rotation.value = item.rotation || 0;
    fields.size.value = item.size;
    fields.style.value = item.style || 'solid';
    fields.arrow.checked = !!item.arrow;
  }

  function editField(field, read) {
    field.addEventListener('input', function () {
      var item = selected();
      if (!item) return;
      read(item);
      touched();
      render();
    });
  }

  editField(fields.label, function (item) { item.label = fields.label.value; });
  editField(fields.note, function (item) { item.note = fields.note.value; });
  editField(fields.side, function (item) { item.side = fields.side.value; });
  editField(fields.symbol, function (item) { item.symbol = fields.symbol.value; });
  editField(fields.hq, function (item) { item.hq = fields.hq.checked; });
  editField(fields.glyph, function (item) {
    item.glyph = fields.glyph.value.toUpperCase().slice(0, catalog.limits.glyph);
    fields.glyph.value = item.glyph;
  });
  editField(fields.rotation, function (item) { item.rotation = Number(fields.rotation.value); });
  editField(fields.size, function (item) { item.size = Number(fields.size.value); });
  editField(fields.style, function (item) { item.style = fields.style.value; });
  editField(fields.arrow, function (item) { item.arrow = fields.arrow.checked; });

  document.getElementById('tmf-delete').addEventListener('click', removeSelected);
  document.getElementById('tmf-front').addEventListener('click', function () {
    var item = selected();
    if (!item) return;
    snapshot();
    state.doc.items.splice(state.selected, 1);
    state.doc.items.push(item);
    touched();
    select(state.doc.items.length - 1);
  });

  function removeSelected() {
    if (state.selected < 0) return;
    snapshot();
    state.doc.items.splice(state.selected, 1);
    touched();
    select(-1);
  }

  /* -- map settings --------------------------------------------------------- */

  var settings = {
    bg: document.getElementById('tmf-bg'),
    opacity: document.getElementById('tmf-bgop'),
    grid: document.getElementById('tmf-grid'),
    cols: document.getElementById('tmf-cols'),
    rows: document.getElementById('tmf-rows'),
    shape: document.getElementById('tmf-shape'),
    terrain: document.getElementById('tmf-terrain'),
    left: document.getElementById('tmf-left'),
    right: document.getElementById('tmf-right'),
    bottom: document.getElementById('tmf-bottom'),
    top: document.getElementById('tmf-top')
  };
  settings.bg.value = state.doc.background.url || '';
  settings.opacity.value = state.doc.background.opacity;
  settings.grid.checked = !!state.doc.grid.show;
  settings.cols.value = state.doc.grid.cols;
  settings.rows.value = state.doc.grid.rows;
  settings.shape.value = state.doc.width + 'x' + state.doc.height;

  fillOptions(settings.terrain, [{ value: '', label: 'Custom corners' }].concat(
    catalog.terrains.map(function (terrain) {
      return { value: terrain.name, label: terrain.name + ' · ' + terrain.size + ' m' };
    })
  ), state.doc.arma.terrain || '');
  ['left', 'right', 'bottom', 'top'].forEach(function (corner) {
    settings[corner].value = state.doc.arma[corner];
  });

  // Picking a terrain fills the corners in with its whole map, which is the
  // common case; typing in a corner afterwards is what a crop needs, and the
  // terrain then only names which map it is.
  settings.terrain.addEventListener('change', function () {
    var chosen = catalog.terrains.filter(function (terrain) {
      return terrain.name === settings.terrain.value;
    })[0];
    if (!chosen) return;
    settings.left.value = 0;
    settings.bottom.value = 0;
    settings.right.value = chosen.size;
    settings.top.value = chosen.size;
    settingsChanged();
  });

  function settingsChanged() {
    state.doc.background.url = settings.bg.value.trim();
    state.doc.background.opacity = Number(settings.opacity.value);
    state.doc.grid.show = settings.grid.checked;
    state.doc.grid.cols = Math.max(1, Number(settings.cols.value) || 10);
    state.doc.grid.rows = Math.max(1, Number(settings.rows.value) || 10);
    state.doc.arma.terrain = settings.terrain.value;
    ['left', 'right', 'bottom', 'top'].forEach(function (corner) {
      state.doc.arma[corner] = Number(settings[corner].value);
    });
    var shape = settings.shape.value.split('x').map(Number);
    var resized = shape[0] !== state.doc.width || shape[1] !== state.doc.height;
    state.doc.width = shape[0];
    state.doc.height = shape[1];
    touched();
    render();
    if (resized) resetView();
  }

  Object.keys(settings).forEach(function (key) {
    settings[key].addEventListener('input', settingsChanged);
    settings[key].addEventListener('change', settingsChanged);
  });

  /* -- saving --------------------------------------------------------------- */

  var saving = false;

  function save() {
    if (saving) return;
    saving = true;
    say('Saving…');
    fetch(app.dataset.save, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ csrf: app.dataset.csrf, doc: state.doc })
    }).then(function (response) {
      return response.json().catch(function () {
        // A refusal comes back as an HTML page — a stale session or a CSRF
        // token from before a redeploy, both of which a reload fixes.
        return { ok: false, error: 'The server refused the save (' +
          response.status + '). Reload the page and try again.' };
      });
    }).then(function (answer) {
      saving = false;
      if (!answer.ok) {
        say(answer.error || 'That did not save.', 'err');
        return;
      }
      state.dirty = false;
      say((answer.notes && answer.notes.length) ? answer.notes.join(' ') : 'Saved', 'ok');
    }).catch(function () {
      saving = false;
      say('Could not reach the server — your drawing is still here, try again.', 'err');
    });
  }

  document.getElementById('tmsave').addEventListener('click', save);
  document.getElementById('tmundo').addEventListener('click', undo);

  function undo() {
    var previous = state.undo.pop();
    if (!previous) {
      say('Nothing left to undo.');
      return;
    }
    state.doc = JSON.parse(previous);
    state.selected = -1;
    touched();
    render();
    fillInspector();
  }

  window.addEventListener('beforeunload', function (event) {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  /* -- keyboard ------------------------------------------------------------- */

  var SHORTCUTS = { v: 'select', u: 'unit', m: 'point', l: 'line', a: 'area', t: 'text' };

  document.addEventListener('keydown', function (event) {
    var tag = (event.target.tagName || '').toLowerCase();
    var typing = tag === 'input' || tag === 'textarea' || tag === 'select';

    // Saving works from anywhere, including mid-word in the label field, which
    // is exactly where somebody reaches for it. Undo does not: inside a field
    // it has to stay the browser's own undo.
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      save();
      return;
    }
    if (typing) return;

    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      undo();
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key === 'Enter' && state.draft) {
      event.preventDefault();
      finishDraft();
    } else if (event.key === 'Escape') {
      state.draft = null;
      overlay.innerHTML = '';
      say('');
      select(-1);
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      if (state.selected >= 0) {
        event.preventDefault();
        removeSelected();
      }
    } else if (SHORTCUTS[event.key.toLowerCase()]) {
      setTool(SHORTCUTS[event.key.toLowerCase()]);
    }
  });

  setTool('select');
  render();
  say('');
}());
