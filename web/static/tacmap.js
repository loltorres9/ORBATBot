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
  // The terrain's own town names. They belong to the terrain rather than to
  // this document, so they arrive separately and are never saved back — the
  // document only carries how much of them to show.
  var placesNode = document.getElementById('tmplaces');
  var places = placesNode ? JSON.parse(placesNode.textContent) : [];
  var state = {
    doc: JSON.parse(docNode.textContent),
    tool: 'select',
    side: 'friend',
    symbol: 'inf',
    glyph: catalog.points.length ? catalog.points[0].glyph : 'OBJ',
    marker: catalog.defaultMarker,
    echelon: '',
    dimension: catalog.defaultDimension,
    status: catalog.defaultStatus,
    layer: '',
    // What a line or an area is painted when it is drawn. '' means the
    // side's own colour, which is what everything did before there was a
    // palette to pick from.
    color: '',
    style: 'solid',
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
  var markers = {};
  catalog.markers.forEach(function (marker) { markers[marker.key] = marker; });
  var echelons = {};
  catalog.echelons.forEach(function (entry) { echelons[entry.key] = entry; });
  var strengths = {};
  catalog.strengths.forEach(function (entry) { strengths[entry.key] = entry; });
  var mobility = {};
  catalog.mobility.forEach(function (entry) { mobility[entry.key] = entry; });
  var statuses = {};
  catalog.statuses.forEach(function (entry) { statuses[entry.key] = entry; });

  function dimensionOf(item) {
    // Which frame a symbol is actually drawn in. A document written by a
    // newer version can name a dimension this one has not got, and the `use`
    // and the geometry have to agree on the fallback or the label would be
    // measured off a frame that is not on the page.
    var shapes = catalog.frames[item.side] || catalog.frames.friend;
    return shapes[item.dimension] ? item.dimension : catalog.defaultDimension;
  }

  function frameOf(item) {
    // Mirrors frame_of() in utils/tacmap.py: side first, then dimension.
    var shapes = catalog.frames[item.side] || catalog.frames.friend;
    return shapes[dimensionOf(item)];
  }

  function dashOf(item) {
    var status = statuses[item.status] || statuses[catalog.defaultStatus];
    return status && status.dash ? ' stroke-dasharray="' + status.dash + '"' : '';
  }

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

  function labelSize(size) {
    return Math.round(Math.max(catalog.minLabel, 14 * size) * 10) / 10;
  }

  function label(text, x, y, size, fill) {
    if (!text) return '';
    var font = labelSize(size);
    return '<text x="' + round(x) + '" y="' + round(y) + '" text-anchor="middle"' +
      ' font-size="' + font + '" font-weight="600"' +
      ' font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif"' +
      ' fill="' + (fill || '#ffffff') + '" stroke="#11161c"' +
      ' stroke-width="' + round(font * 0.22) + '"' +
      ' paint-order="stroke" stroke-linejoin="round">' + esc(text) + '</text>';
  }

  function itemColour(item) {
    return item.color || colours[item.side].fill;
  }

  var FONT = 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif';

  function chromeText(text, x, y, size, paint, anchor) {
    // Mirrors _chrome_text() in utils/tacmap.py: dark over a pale outline, so
    // what sits outside the frame reads on terrain of any colour.
    return '<text x="' + round(x) + '" y="' + round(y) + '" text-anchor="' +
      (anchor || 'middle') + '" font-size="' + size + '" font-weight="700"' +
      ' font-family="' + FONT + '" fill="' + paint.glyph + '" stroke="' +
      paint.fill + '" stroke-width="' + round(size * 0.3) +
      '" paint-order="stroke" stroke-linejoin="round">' + esc(text) + '</text>';
  }

  function modifierSVG(item, paint) {
    // Mirrors _modifier_svg() in utils/tacmap.py.
    var parts = [];
    var frame = frameOf(item);
    var line = frame.top - catalog.echelonLift;
    var hasEchelon = !!echelons[item.echelon];
    if (hasEchelon) {
      var shift = 'translate(0,' + round(line - 50) + ')';
      [[paint.fill, 15], [paint.glyph, 7]].forEach(function (pass) {
        parts.push('<use href="#tmx-' + item.echelon + '" fill="none" stroke="' +
          pass[0] + '" color="' + pass[0] + '" stroke-width="' + pass[1] +
          '" stroke-linecap="round" transform="' + shift + '"/>');
      });
    }
    if (strengths[item.strength]) {
      parts.push(chromeText(strengths[item.strength].text,
        hasEchelon ? 103 : 50, line + 7, 20, paint));
    }
    if (item.higher) {
      parts.push(chromeText(item.higher, -3, line + 7, 20, paint, 'end'));
    }
    if (mobility[item.mobility]) {
      var under = 'translate(0,' + round(frame.bottom + catalog.mobilityDrop) + ')';
      [[paint.fill, 14], [paint.glyph, 6]].forEach(function (pass) {
        parts.push('<use href="#tmv-' + item.mobility + '" fill="none" stroke="' +
          pass[0] + '" color="' + pass[0] + '" stroke-width="' + pass[1] +
          '" stroke-linecap="round" stroke-linejoin="round" transform="' +
          under + '"/>');
      });
    }
    if (item.text) {
      if ((symbols[item.symbol] || {}).hasIcon) {
        parts.push(chromeText(item.text, 102, 57, 22, paint, 'start'));
      } else {
        parts.push('<text x="50" y="61" text-anchor="middle" font-size="30"' +
          ' font-weight="700" font-family="' + FONT + '" fill="' + paint.glyph +
          '">' + esc(item.text) + '</text>');
      }
    }
    return parts.join('');
  }

  function unitSVG(item) {
    var paint = colours[item.side];
    var scale = catalog.unitBox * item.size / 100;
    var transform = 'translate(' + round(item.x) + ',' + round(item.y) + ') rotate(' +
      (item.rotation || 0) + ') scale(' + scale + ') translate(-50,-50)';
    var dimension = dimensionOf(item);
    var parts = ['<use href="#tmf-' + item.side + '-' + dimension + '" fill="' +
      paint.fill + '" stroke="' + paint.edge + '" stroke-width="5"' +
      dashOf(item) + '/>'];
    if (item.hq) {
      parts.push('<use href="#tmh-' + item.side + '-' + dimension +
        '" stroke="' + paint.edge + '" stroke-width="5"/>');
    }
    if ((symbols[item.symbol] || {}).hasIcon) {
      parts.push('<use href="#tmi-' + item.symbol + '" fill="none" stroke="' +
        paint.glyph + '" color="' + paint.glyph + '" stroke-width="7"' +
        ' stroke-linecap="round" stroke-linejoin="round"/>');
    }
    parts.push(modifierSVG(item, paint));
    var depth = frameOf(item).bottom;
    if (mobility[item.mobility]) {
      depth += catalog.mobilityDrop + catalog.mobilityDepth;
    }
    depth = Math.max(depth, item.hq ? 135 : 112);
    var drop = catalog.unitBox * item.size * (depth - 50) / 100 + 6;
    return '<g transform="' + transform + '">' + parts.join('') + '</g>' +
      label(item.label, item.x, item.y + drop, item.size);
  }

  function markerKey(item) {
    return markers[item.marker] ? item.marker : catalog.defaultMarker;
  }

  function pointSVG(item) {
    // Mirrors _point_svg() in utils/tacmap.py: line art in the side's colour,
    // drawn twice so the shape holds up over terrain.
    var paint = colours[item.side];
    var key = markerKey(item);
    var scale = catalog.pointBox * item.size / 100 * 1.55;
    var transform = 'translate(' + round(item.x) + ',' + round(item.y) + ') scale(' +
      scale + ') translate(-50,-50)';
    var dash = dashOf(item);
    var shape = '<g transform="' + transform + '">' +
      '<use href="#tmm-' + key + '" fill="none" stroke="' + paint.edge +
      '" color="' + paint.edge + '" stroke-width="15"' +
      ' stroke-linecap="round" stroke-linejoin="round"' + dash + '/>' +
      '<use href="#tmm-' + key + '" fill="none" stroke="' + paint.fill +
      '" color="' + paint.fill + '" stroke-width="7"' +
      ' stroke-linecap="round" stroke-linejoin="round"' + dash + '/></g>';
    var parts = [shape];
    var glyph = item.glyph || '';
    var name = item.label;
    if (glyph && markers[key].text) {
      parts.push(label(glyph, item.x, item.y + labelSize(item.size) * 0.36,
        item.size * 0.9));
    } else if (glyph) {
      name = (glyph + ' ' + name).trim();
    }
    parts.push(label(name, item.x, item.y + catalog.pointBox * item.size * 0.8 + 8,
      item.size));
    return parts.join('');
  }

  function arrowHead(points, size) {
    // Mirrors arrow_head() in utils/tacmap.py — a polygon rather than a marker,
    // because a marker cannot take the colour of the line it sits on.
    var from = points[points.length - 2];
    var to = points[points.length - 1];
    var length = Math.hypot(to[0] - from[0], to[1] - from[1]);
    if (!length) return null;
    var ax = (to[0] - from[0]) / length;
    var ay = (to[1] - from[1]) / length;
    var back = 13 * size;
    var wide = 5.5 * size;
    return [
      [round(to[0] + ax * 2 * size), round(to[1] + ay * 2 * size)],
      [round(to[0] - ax * back - ay * wide), round(to[1] - ay * back + ax * wide)],
      [round(to[0] - ax * back + ay * wide), round(to[1] - ay * back - ax * wide)]
    ];
  }

  function shapeSVG(item) {
    var colour = itemColour(item);
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
      return '<polygon points="' + points + '" fill="' + colour +
        '" fill-opacity="0.22" stroke="' + colour + '" stroke-width="' + width +
        '"' + dash + ' stroke-linejoin="round"/>' +
        label(item.label, centre[0], centre[1], item.size);
    }
    var parts = ['<polyline points="' + points + '" fill="none" stroke="' + colour +
      '" stroke-width="' + width + '"' + dash +
      ' stroke-linecap="round" stroke-linejoin="round"/>'];
    if (item.arrow) {
      var head = arrowHead(item.points, item.size);
      if (head) {
        parts.push('<polygon points="' + head.map(function (corner) {
          return corner[0] + ',' + corner[1];
        }).join(' ') + '" fill="' + colour + '"/>');
      }
    }
    if (item.label) {
      var middle = item.points[Math.floor(item.points.length / 2)];
      parts.push(label(item.label, middle[0], middle[1] - 10 * item.size, item.size));
    }
    return parts.join('');
  }

  function itemSVG(item) {
    if (item.kind === 'unit') return unitSVG(item);
    if (item.kind === 'point') return pointSVG(item);
    if (item.kind === 'text') {
      return label(item.label || ' ', item.x, item.y, item.size * 1.6, item.color);
    }
    return shapeSVG(item);
  }

  function hitSVG(item) {
    // A hair-thin line is impossible to grab, so every line and area carries an
    // invisible fat copy of itself for the pointer to land on. A task marker
    // needs one too: it is line art with no fill, so without this a click in
    // the middle of an X or a circle lands on the terrain behind it.
    if (item.kind === 'point') {
      return '<circle cx="' + round(item.x) + '" cy="' + round(item.y) + '" r="' +
        round(catalog.pointBox * item.size * 0.8) +
        '" fill="#000" fill-opacity="0"/>';
    }
    if (item.kind !== 'line' && item.kind !== 'area') return '';
    var points = item.points.map(function (point) {
      return round(point[0]) + ',' + round(point[1]);
    }).join(' ');
    return '<polyline points="' + points + '" fill="none" stroke="#000"' +
      ' stroke-opacity="0" stroke-width="' + round(20 * item.size) + '"/>';
  }

  function emptySheet() {
    return '<rect x="0" y="0" width="' + state.doc.width + '" height="' +
      state.doc.height + '" fill="#20262e"/>';
  }

  function tilesSVG() {
    var background = state.doc.background;
    var perSide = Math.pow(2, background.zoom);
    var width = state.doc.width / perSide;
    var height = state.doc.height / perSide;
    var tiles = [];
    // Mirrors _tiles_svg() in utils/tacmap.py: a coarse copy of the terrain
    // under the grid, then every tile at its own exact size. Stretching a tile
    // to overlap its neighbour is what made features jump at a boundary.
    if (background.zoom > catalog.backdropZoom) {
      tiles.push('<image href="' + attr(background.url + '/' +
        catalog.backdropZoom + '/0/0.png') +
        '" x="0" y="0" width="' + state.doc.width +
        '" height="' + state.doc.height + '" preserveAspectRatio="none"/>');
    }
    for (var column = 0; column < perSide; column++) {
      for (var row = 0; row < perSide; row++) {
        tiles.push('<image href="' + attr(background.url + '/' + background.zoom +
          '/' + column + '/' + row + '.png') +
          '" x="' + round(column * width) + '" y="' + round(row * height) +
          '" width="' + round(width) + '" height="' + round(height) +
          '" preserveAspectRatio="none"/>');
      }
    }
    return emptySheet() + '<g opacity="' + background.opacity + '">' +
      tiles.join('') + '</g>';
  }

  function backgroundSVG() {
    var background = state.doc.background;
    if (!background.url) return emptySheet();
    if (background.kind === 'tiles') return tilesSVG();
    return '<image href="' + attr(background.url) + '" x="0" y="0" width="' +
      state.doc.width + '" height="' + state.doc.height + '" opacity="' +
      background.opacity + '" preserveAspectRatio="none"/>';
  }

  // Arma's world metres as a point on the sheet — the mirror of
  // tacmap.from_world(). The sheet's y grows down and Arma's grows north, so
  // the vertical axis flips.
  function fromWorld(wx, wy) {
    var arma = state.doc.arma || {};
    var spanX = (arma.right - arma.left) || 1;
    var spanY = (arma.top - arma.bottom) || 1;
    return [(wx - arma.left) / spanX * state.doc.width,
            (arma.top - wy) / spanY * state.doc.height];
  }

  // The mirror of tacmap.places_svg(). Kept in step with it by hand, the same
  // bargain itemSVG() makes: the shapes live in the defs, and what is written
  // twice is the composition.
  function placesSVG() {
    var shows = state.doc.places || {};
    if (!shows.show || !places.length) return '';
    var groups = shows.groups || [];
    var scale = Math.max(0.4, Math.min(Number(shows.scale) || 1, 3));
    var shown = [];
    places.forEach(function (place) {
      var spec = catalog.placeKinds[place.kind] || catalog.placeKinds[catalog.defaultPlaceKind];
      if (groups.length && groups.indexOf(spec.group) < 0) return;
      var point = fromWorld(place.x, place.y);
      if (point[0] < 0 || point[0] > state.doc.width) return;
      if (point[1] < 0 || point[1] > state.doc.height) return;
      shown.push({ name: place.name, rank: spec.rank, x: point[0], y: point[1] });
    });
    // Biggest last, so where two collide the one people navigate by survives.
    shown.sort(function (a, b) { return a.rank - b.rank; });

    return '<g class="tm-places" pointer-events="none">' + shown.map(function (place) {
      var size = Math.max((catalog.placeSizes[place.rank] || 7) * scale, 5.4);
      var common = 'x="' + round(place.x) + '" y="' + round(place.y) +
        '" text-anchor="middle" font-size="' + round(size) +
        '" font-family="inherit"' + (place.rank >= 4 ? ' letter-spacing="1.2"' : '');
      return '<text ' + common + ' stroke="#ffffff" stroke-width="' +
        round(size / 4) + '" stroke-linejoin="round" fill="none" opacity="0.85">' +
        esc(place.name) + '</text>' +
        '<text ' + common + ' fill="#1b2027">' + esc(place.name) + '</text>';
    }).join('') + '</g>';
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
    return '<' + tag + ' points="' + points + '" fill="none" stroke="' + paint.fill +
      '" stroke-width="4" stroke-dasharray="10 8" stroke-linecap="round"/>';
  }

  function layers() {
    if (!state.doc.layers || !state.doc.layers.length) {
      state.doc.layers = [{ id: 'plan', name: 'Plan', visible: true }];
    }
    return state.doc.layers;
  }

  function layerRank(id) {
    var found = layers().findIndex(function (entry) { return entry.id === id; });
    return found < 0 ? 0 : found;
  }

  function hiddenLayers() {
    var hidden = {};
    layers().forEach(function (entry) { if (!entry.visible) hidden[entry.id] = true; });
    return hidden;
  }

  // Mirrors ordered_items() in utils/tacmap.py: layer, then kind, then the
  // document's own order — so a symbol is never buried under an area drawn
  // after it, and Bring to front still decides between two of the same kind.
  function drawOrder() {
    var hidden = hiddenLayers();
    var order = [];
    state.doc.items.forEach(function (item, index) {
      if (!hidden[item.layer]) order.push(index);
    });
    return order.sort(function (a, b) {
      var first = state.doc.items[a];
      var second = state.doc.items[b];
      return (layerRank(first.layer) - layerRank(second.layer)) ||
        ((catalog.kindOrder[first.kind] || 0) - (catalog.kindOrder[second.kind] || 0)) ||
        (a - b);
    });
  }

  function render() {
    layer.innerHTML = drawOrder().map(function (index) {
      var item = state.doc.items[index];
      return '<g class="tm-item" data-i="' + index + '">' + itemSVG(item) + hitSVG(item) + '</g>';
    }).join('');
    back.innerHTML = backgroundSVG() + gridSVG() + placesSVG();
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

  /* -- layers: a switch each, for whoever is looking ------------------------ */

  var layerPanel = document.getElementById('tmlayers');
  var layerList = document.getElementById('tmlayerlist');

  function drawLayers() {
    if (!layerPanel) return;
    // One layer and no way to add another is nothing worth a panel — and with
    // nothing else in it, the sidebar is only taking room from the map.
    layerPanel.hidden = !editable && layers().length < 2;
    var aside = app.querySelector('.tmside');
    if (aside && !editable) aside.hidden = layerPanel.hidden;
    layerList.innerHTML = '';
    layers().forEach(function (entry, index) {
      var row = document.createElement('li');
      row.className = 'tmlayer';

      var shown = document.createElement('input');
      shown.type = 'checkbox';
      shown.checked = entry.visible !== false;
      shown.title = 'Show this layer';
      shown.addEventListener('change', function () {
        entry.visible = shown.checked;
        if (editable) touched();
        render();
      });
      row.appendChild(shown);

      if (editable) {
        var name = document.createElement('input');
        name.className = 'tmlayername';
        name.value = entry.name;
        name.maxLength = catalog.limits.layerName;
        name.addEventListener('input', function () {
          entry.name = name.value;
          touched();
          fillInspector();
        });
        row.appendChild(name);

        if (layers().length > 1) {
          var drop = document.createElement('button');
          drop.type = 'button';
          drop.className = 'linkish';
          drop.textContent = '✕';
          drop.title = 'Delete this layer — what is on it moves to the first one';
          drop.addEventListener('click', function () { removeLayer(index); });
          row.appendChild(drop);
        }
      } else {
        var text = document.createElement('span');
        text.textContent = entry.name;
        row.appendChild(text);
      }
      layerList.appendChild(row);
    });
  }

  function removeLayer(index) {
    var list = layers();
    if (list.length < 2) return;
    var going = list[index];
    var keep = list[index === 0 ? 1 : 0].id;
    snapshot();
    // Never take the items with it: a layer is a switch, not a folder anybody
    // meant to throw work into.
    state.doc.items.forEach(function (item) {
      if (item.layer === going.id) item.layer = keep;
    });
    list.splice(index, 1);
    touched();
    render();
    drawLayers();
    fillInspector();
  }

  /* -- more room: the map takes the whole screen ---------------------------- */

  var fullButton = document.getElementById('tmfull');
  if (fullButton) {
    fullButton.addEventListener('click', function () {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else if (app.requestFullscreen) {
        app.requestFullscreen().catch(function () { app.classList.toggle('tm-tall'); });
      } else {
        app.classList.toggle('tm-tall');
      }
    });
    document.addEventListener('fullscreenchange', function () {
      fullButton.textContent = document.fullscreenElement ? '✕ Close' : '⛶ Full screen';
    });
  }

  /* -- everything below is the editor and only runs when it may draw -------- */

  if (!editable) {
    drawLayers();
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
    var item = { kind: kind, side: state.side, layer: state.layer, label: '',
                 note: '', size: 1 };
    if (kind === 'unit') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.symbol = state.symbol;
      item.hq = state.hq;
      item.rotation = 0;
      item.echelon = state.echelon;
      item.strength = '';
      item.text = '';
      item.higher = '';
      item.mobility = '';
      item.dimension = state.dimension;
      item.status = state.status;
    } else if (kind === 'point') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.glyph = state.glyph;
      item.marker = state.marker;
      item.status = state.status;
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

    // Dragging is the only way to draw a line, and it is the gesture Arma's
    // own map uses. Ctrl does it from any tool — including Select, so you
    // never have to leave what you are doing — and the Line and Area tools do
    // it without Ctrl. Clicking a line out corner by corner is gone: it was a
    // mode you had to finish before anything else worked.
    var drawing = state.tool === 'line' || state.tool === 'area';
    if (event.ctrlKey || event.metaKey || drawing) {
      event.preventDefault();
      state.draft = {
        kind: state.tool === 'area' ? 'area' : 'line',
        points: [[round(point.x), round(point.y)]],
        cursor: null
      };
      overlay.innerHTML = draftSVG();
      say('Drawing — let go to finish');
      svg.setPointerCapture(event.pointerId);
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
      var here = at(event);
      var points = state.draft.points;
      var last = points[points.length - 1];
      // Thinned as it is drawn rather than afterwards: a pointer reports
      // every pixel it passes and the document takes MAX_POINTS of them, so
      // an unthinned stroke would hit the cap within one gesture. The step
      // is a fraction of what is on screen, so it thins by how far the line
      // actually looks, not by how far zoomed in it happens to be.
      var step = view.w / 90;
      if (Math.hypot(here.x - last[0], here.y - last[1]) < step) return;
      if (points.length >= catalog.limits.points) return;
      points.push([round(here.x), round(here.y)]);
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
    if (state.draft) {
      finishDraft();
      return;
    }
    if (state.drag && state.drag.moved) touched();
    state.drag = null;
    state.pan = null;
  });

  // Letting go outside the sheet still ends the stroke; without this the
  // draft would hang around and the next click would extend it.
  svg.addEventListener('pointercancel', function () {
    if (state.draft) finishDraft();
  });

  function finishDraft() {
    var draft = state.draft;
    state.draft = null;
    say('');
    overlay.innerHTML = '';
    if (!draft) return;
    // A stroke that went nowhere is a slipped finger, not an error worth
    // a message: there is no deliberate way to draw a two-point line any more.
    if (draft.points.length < (draft.kind === 'area' ? 3 : 2)) return;
    add({
      kind: draft.kind, side: state.side, layer: state.layer, label: '', note: '',
      size: 1, color: state.color, points: draft.points, style: state.style,
      // Never automatically: an arrow says "this way", and most lines on a
      // plan are boundaries and phase lines that say no such thing. The
      // inspector puts one on the lines that mean it.
      arrow: false
    });
  }

  /* -- toolbar -------------------------------------------------------------- */

  function setTool(tool) {
    if (state.draft) finishDraft();
    state.tool = tool;
    Array.prototype.forEach.call(app.querySelectorAll('.tmtool'), function (button) {
      button.classList.toggle('active', button.dataset.tool === tool);
    });
    // `data-for` is a list, the same as in the inspector: a field can belong
    // to more than one tool. Comparing the whole attribute silently hid every
    // such field — which is what happened to the State picker, whose
    // `data-for="unit point"` matched neither.
    Array.prototype.forEach.call(app.querySelectorAll('.tmpick [data-for]'), function (field) {
      field.hidden = field.dataset.for.split(' ').indexOf(tool) < 0;
    });
    svg.classList.toggle('tm-drawing', tool !== 'select');
  }

  Array.prototype.forEach.call(app.querySelectorAll('.tmtool'), function (button) {
    button.addEventListener('click', function () { setTool(button.dataset.tool); });
  });

  function swatchRow(host, read, write) {
    // A row of colour buttons, the first of which hands the item back to its
    // side's own colour. Buttons rather than a <select>, because picking a
    // colour from a list of names is exactly the thing a swatch avoids.
    var buttons = [{ value: '', label: "The side's own" }].concat(
      catalog.lineColours.map(function (entry) {
        return { value: entry.value, label: entry.label };
      }));
    host.innerHTML = buttons.map(function (entry) {
      return '<button type="button" class="tmswatch' +
        (entry.value ? '' : ' tmswatch-side') + '" data-color="' +
        attr(entry.value) + '" title="' + attr(entry.label) + '"' +
        (entry.value ? ' style="background:' + attr(entry.value) + '"' : '') +
        '><span class="visually-hidden">' + esc(entry.label) + '</span></button>';
    }).join('');
    host.addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('.tmswatch') : null;
      if (!button) return;
      event.preventDefault();
      write(button.dataset.color);
      markSwatches(host, read());
    });
    markSwatches(host, read());
  }

  function markSwatches(host, value) {
    Array.prototype.forEach.call(host.querySelectorAll('.tmswatch'), function (b) {
      b.classList.toggle('active', b.dataset.color === (value || ''));
    });
  }

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
  var markerOptions = catalog.markers.map(function (marker) {
    return { value: marker.key, label: marker.label };
  });
  var echelonOptions = [{ value: '', label: 'No size' }].concat(
    catalog.echelons.map(function (entry) {
      return { value: entry.key, label: entry.label };
    }));
  var strengthOptions = [{ value: '', label: 'As it stands' }].concat(
    catalog.strengths.map(function (entry) {
      return { value: entry.key, label: entry.label };
    }));
  var mobilityOptions = [{ value: '', label: 'Not stated' }].concat(
    catalog.mobility.map(function (entry) {
      return { value: entry.key, label: entry.label };
    }));
  var dimensionOptions = catalog.dimensions.map(function (entry) {
    return { value: entry.key, label: entry.label };
  });
  var statusOptions = catalog.statuses.map(function (entry) {
    return { value: entry.key, label: entry.label };
  });

  var sidePick = document.getElementById('tmside');
  var symbolPick = document.getElementById('tmsymbol');
  var glyphPick = document.getElementById('tmglyph');
  var markerPick = document.getElementById('tmmarker');
  var echelonPick = document.getElementById('tmechelon');
  var hqPick = document.getElementById('tmhq');
  var dimensionPick = document.getElementById('tmdimension');
  var statusPick = document.getElementById('tmstatuspick');

  fillOptions(sidePick, sideOptions, state.side);
  fillOptions(symbolPick, symbolOptions, state.symbol);
  fillOptions(glyphPick, glyphOptions, state.glyph);
  fillOptions(markerPick, markerOptions, state.marker);
  fillOptions(echelonPick, echelonOptions, state.echelon);
  fillOptions(dimensionPick, dimensionOptions, state.dimension);
  fillOptions(statusPick, statusOptions, state.status);

  var layerPick = document.getElementById('tmlayerpick');
  var layerAdd = document.getElementById('tmlayeradd');
  var layerName = document.getElementById('tmlayernew');

  function slug(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '').slice(0, 24);
  }

  function fillLayerPickers() {
    var options = layers().map(function (entry) {
      return { value: entry.id, label: entry.name };
    });
    fillOptions(layerPick, options, state.layer);
    fillOptions(fields.layer, options, state.layer);
    var item = selected();
    if (item) fields.layer.value = item.layer;
  }

  function addLayer(name) {
    var list = layers();
    if (list.length >= catalog.limits.layers) {
      say('That is as many layers as one map holds.', 'err');
      return;
    }
    var base = slug(name) || 'layer';
    var id = base;
    var suffix = 2;
    while (list.some(function (entry) { return entry.id === id; })) {
      id = base + '-' + (suffix++);
    }
    snapshot();
    list.push({ id: id, name: (name || base).slice(0, catalog.limits.layerName),
                visible: true });
    state.layer = id;
    touched();
    drawLayers();
    fillLayerPickers();
  }

  if (layerAdd) {
    layerAdd.addEventListener('click', function () {
      addLayer(layerName.value.trim());
      layerName.value = '';
    });
  }
  layerPick.addEventListener('change', function () { state.layer = layerPick.value; });

  sidePick.addEventListener('change', function () { state.side = sidePick.value; });
  symbolPick.addEventListener('change', function () { state.symbol = symbolPick.value; });
  glyphPick.addEventListener('change', function () { state.glyph = glyphPick.value; });
  markerPick.addEventListener('change', function () { state.marker = markerPick.value; });
  echelonPick.addEventListener('change', function () { state.echelon = echelonPick.value; });
  hqPick.addEventListener('change', function () { state.hq = hqPick.checked; });
  dimensionPick.addEventListener('change', function () {
    state.dimension = dimensionPick.value;
  });
  statusPick.addEventListener('change', function () { state.status = statusPick.value; });

  var colourRow = document.getElementById('tmcolours');
  if (colourRow) {
    swatchRow(colourRow,
      function () { return state.color; },
      function (value) { state.color = value; });
  }
  var stylePick = document.getElementById('tmstyle');
  if (stylePick) {
    stylePick.value = state.style;
    stylePick.addEventListener('change', function () { state.style = stylePick.value; });
  }

  /* -- the inspector -------------------------------------------------------- */

  var fields = {
    label: document.getElementById('tmf-label'),
    note: document.getElementById('tmf-note'),
    side: document.getElementById('tmf-side'),
    symbol: document.getElementById('tmf-symbol'),
    hq: document.getElementById('tmf-hq'),
    glyph: document.getElementById('tmf-glyph'),
    marker: document.getElementById('tmf-marker'),
    echelon: document.getElementById('tmf-echelon'),
    strength: document.getElementById('tmf-strength'),
    mobility: document.getElementById('tmf-mobility'),
    dimension: document.getElementById('tmf-dimension'),
    status: document.getElementById('tmf-status'),
    higher: document.getElementById('tmf-higher'),
    text: document.getElementById('tmf-text'),
    rotation: document.getElementById('tmf-rotation'),
    size: document.getElementById('tmf-size'),
    style: document.getElementById('tmf-style'),
    arrow: document.getElementById('tmf-arrow'),
    layer: document.getElementById('tmf-layer'),
    color: document.getElementById('tmf-color')
  };
  fillOptions(fields.side, sideOptions, state.side);
  fillOptions(fields.symbol, symbolOptions, state.symbol);
  fillOptions(fields.marker, markerOptions, state.marker);
  fillOptions(fields.echelon, echelonOptions, state.echelon);
  fillOptions(fields.strength, strengthOptions, '');
  fillOptions(fields.mobility, mobilityOptions, '');
  fillOptions(fields.dimension, dimensionOptions, catalog.defaultDimension);
  fillOptions(fields.status, statusOptions, catalog.defaultStatus);

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
    fields.marker.value = markerKey(item);
    fields.echelon.value = item.echelon || '';
    fields.strength.value = item.strength || '';
    fields.mobility.value = item.mobility || '';
    fields.dimension.value = dimensionOf(item);
    fields.status.value = statuses[item.status] ? item.status : catalog.defaultStatus;
    fields.higher.value = item.higher || '';
    fields.text.value = item.text || '';
    fields.rotation.value = item.rotation || 0;
    fields.size.value = item.size;
    fields.style.value = item.style || 'solid';
    fields.arrow.checked = !!item.arrow;
    fields.layer.value = item.layer;
    fields.color.value = item.color || colours[item.side].fill;
    if (inspectorColours) markSwatches(inspectorColours, item.color);
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
  [['echelon', fields.echelon], ['strength', fields.strength],
   ['mobility', fields.mobility], ['dimension', fields.dimension],
   ['status', fields.status]].forEach(
    function (pair) {
      pair[1].addEventListener('change', function () {
        var item = selected();
        if (!item) return;
        item[pair[0]] = pair[1].value;
        touched();
        render();
      });
    });

  editField(fields.text, function (item) {
    item.text = fields.text.value.slice(0, catalog.limits.mod);
  });
  editField(fields.higher, function (item) {
    item.higher = fields.higher.value.slice(0, catalog.limits.mod);
  });

  fields.marker.addEventListener('change', function () {
    var item = selected();
    if (!item) return;
    item.marker = fields.marker.value;
    touched();
    render();
  });

  editField(fields.glyph, function (item) {
    item.glyph = fields.glyph.value.toUpperCase().slice(0, catalog.limits.glyph);
    fields.glyph.value = item.glyph;
  });
  editField(fields.rotation, function (item) { item.rotation = Number(fields.rotation.value); });
  editField(fields.size, function (item) { item.size = Number(fields.size.value); });
  editField(fields.style, function (item) { item.style = fields.style.value; });
  editField(fields.arrow, function (item) { item.arrow = fields.arrow.checked; });
  editField(fields.layer, function (item) { item.layer = fields.layer.value; });
  editField(fields.color, function (item) { item.color = fields.color.value; });
  var inspectorColours = document.getElementById('tmf-colours');
  if (inspectorColours) {
    swatchRow(inspectorColours,
      function () { var item = selected(); return item ? item.color : ''; },
      function (value) {
        var item = selected();
        if (!item) return;
        item.color = value;
        fields.color.value = value || colours[item.side].fill;
        touched();
        render();
      });
  }

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
    places: document.getElementById('tmf-places'),
    placeScale: document.getElementById('tmf-placescale'),
    // A container, listened to for the group checkboxes bubbling out of it.
    placeGroups: document.getElementById('tmf-placegroups'),
    cols: document.getElementById('tmf-cols'),
    rows: document.getElementById('tmf-rows'),
    shape: document.getElementById('tmf-shape'),
    zoom: document.getElementById('tmf-zoom'),
    terrain: document.getElementById('tmf-terrain'),
    left: document.getElementById('tmf-left'),
    right: document.getElementById('tmf-right'),
    bottom: document.getElementById('tmf-bottom'),
    top: document.getElementById('tmf-top')
  };
  var tiled = state.doc.background.kind === 'tiles';
  settings.bg.value = tiled ? '' : (state.doc.background.url || '');
  settings.zoom.value = state.doc.background.zoom;
  settings.zoom.max = state.doc.background.max_zoom;
  document.getElementById('tmf-tiles').value = state.doc.background.url || '';
  Array.prototype.forEach.call(app.querySelectorAll('[data-bg]'), function (field) {
    field.hidden = (field.dataset.bg === 'tiles') !== tiled;
  });
  settings.opacity.value = state.doc.background.opacity;
  settings.grid.checked = !!state.doc.grid.show;

  // The place-name controls. A terrain with no names has nothing to switch, so
  // the whole block says why instead of offering dead checkboxes.
  if (!state.doc.places) state.doc.places = { show: true, groups: [], scale: 1 };
  settings.places.checked = !!state.doc.places.show;
  settings.placeScale.value = state.doc.places.scale || 1;
  var placeBox = document.getElementById('tmf-placebox');
  var placeNote = document.getElementById('tmf-placenote');
  if (!places.length) {
    settings.places.disabled = true;
    placeBox.hidden = true;
    // A map on an OCAP server has no terrain stored here to hang names off, so
    // pointing at the Terrains page would be an instruction that leads nowhere.
    var stored = state.doc.background.kind === 'tiles' &&
      /^\/t\/\d+$/.test(state.doc.background.url || '');
    placeNote.textContent = stored
      ? 'This terrain has no place names yet — the Terrains page has the ' +
        'script that copies them out of a mission.'
      : 'Place names come from an uploaded terrain. This map is not on one.';
    placeNote.hidden = false;
  } else {
    var counted = {};
    places.forEach(function (place) {
      var spec = catalog.placeKinds[place.kind] || catalog.placeKinds[catalog.defaultPlaceKind];
      counted[spec.group] = (counted[spec.group] || 0) + 1;
    });
    // Ticking nothing means all of them, which is what an empty list means to
    // the renderer on both sides — so the boxes start ticked rather than
    // showing every group as off while every group is drawn.
    var chosen = state.doc.places.groups || [];
    settings.placeGroups.innerHTML = catalog.placeGroups.filter(function (group) {
      return counted[group.key];
    }).map(function (group) {
      var on = !chosen.length || chosen.indexOf(group.key) >= 0;
      return '<label class="field tmcheck"><input type="checkbox" data-placegroup="' +
        group.key + '"' + (on ? ' checked' : '') + '> <span>' + esc(group.label) +
        ' <span class="muted">' + counted[group.key] + '</span></span></label>';
    }).join('');
    placeNote.textContent = places.length + ' place names on this terrain.';
  }
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
    // A tile set's address comes from the OCAP import and is not typed here,
    // so only an image background reads the URL field back.
    if (state.doc.background.kind !== 'tiles') {
      state.doc.background.url = settings.bg.value.trim();
    }
    state.doc.background.zoom = Math.max(0, Math.min(
      Number(settings.zoom.value) || 0, state.doc.background.max_zoom));
    state.doc.background.opacity = Number(settings.opacity.value);
    state.doc.grid.show = settings.grid.checked;
    state.doc.places.show = settings.places.checked;
    state.doc.places.scale = Number(settings.placeScale.value) || 1;
    var boxes = Array.prototype.slice.call(
      settings.placeGroups.querySelectorAll('[data-placegroup]'));
    var picked = boxes.filter(function (box) { return box.checked; })
      .map(function (box) { return box.dataset.placegroup; });
    // All ticked is the same thing as none ticked, and the empty list is what
    // survives a terrain gaining a new group of names later.
    state.doc.places.groups = picked.length === boxes.length ? [] : picked;
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
    if (event.key === 'Escape' && state.draft) {
      // Before the modifier guard below: a ctrl-drag is cancelled with the
      // key still held down, so a draft would otherwise be unescapable.
      state.draft = null;
      overlay.innerHTML = '';
      say('');
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key === 'Escape') {
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

  fields.size.min = catalog.limits.minSize;
  fields.size.max = catalog.limits.maxSize;

  state.layer = layers()[0].id;
  drawLayers();
  fillLayerPickers();
  setTool('select');
  render();
  say('');
}());
