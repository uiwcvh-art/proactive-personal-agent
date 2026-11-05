'use strict';

// A single motion owner keeps interrupted navigation, sheets and gestures in sync.
// Only transforms/opacity animate; content and scroll positions remain live.
window.PulseMotion = (() => {
  const reduceQuery = matchMedia('(prefers-reduced-motion: reduce)');
  const ease = 'cubic-bezier(.22,.78,.18,1)';
  const routeEase = 'cubic-bezier(.4,0,.2,1)';
  const settle = 'cubic-bezier(.16,1,.3,1)';
  let preferences = () => ({}), haptic = () => {};
  let routeTicket = 0, routeAnimations = [], ghost = null, draggingBack = false, routePreparing = false;
  let sheetTicket = 0, sheetAnimation = null, sheetClosing = false, sheetDrag = null;
  let lastHaptic = 0, suppressClickUntil = 0;
  let backOptions = null, systemBack = null;
  const shell = () => document.querySelector('.app-shell:not(.route-ghost)');
  const reduced = () => reduceQuery.matches || preferences().motion === false;
  const done = animation => animation.finished.catch(() => {});
  const frame = () => new Promise(resolve => requestAnimationFrame(resolve));

  function feedback(kind = 'tap') {
    if (preferences().haptics === false || performance.now() - lastHaptic < 60) return;
    lastHaptic = performance.now();
    haptic(kind);
  }

  function cleanRoute() {
    routeTicket++;
    draggingBack = false;
    routePreparing = false;
    systemBack = null;
    routeAnimations.forEach(animation => animation.cancel());
    routeAnimations = [];
    ghost?.remove();
    ghost = null;
    const live = shell();
    if (live) {
      live.style.removeProperty('transform');
      live.style.removeProperty('z-index');
      live.style.removeProperty('will-change');
      live.classList.remove('route-moving');
    }
  }

  function copyTree(source) {
    const copy = source.cloneNode(true);
    const sourceNodes = [source, ...source.querySelectorAll('*')];
    const copyNodes = [copy, ...copy.querySelectorAll('*')];
    const scrolls = [];
    sourceNodes.forEach((node, index) => {
      const target = copyNodes[index];
      if (node.scrollTop || node.scrollLeft) scrolls.push([target, node.scrollTop, node.scrollLeft]);
      if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) target.value = node.value;
      if (node instanceof HTMLSelectElement) target.selectedIndex = node.selectedIndex;
      if (target.id) {
        target.dataset.visualId = target.id;
        target.removeAttribute('id');
      }
      target.removeAttribute('autofocus');
    });
    return {copy, scrolls};
  }

  function capture() {
    cleanRoute();
    const live = shell();
    if (!live || reduced()) return null;
    const visual = live.cloneNode(false), scrolls = [];
    for (const header of live.querySelectorAll(':scope > header:not([hidden])')) {
      const tree = copyTree(header); visual.append(tree.copy); scrolls.push(...tree.scrolls);
    }
    const main = document.createElement('main');
    const page = live.querySelector('.page.active');
    if (page) {
      const tree = copyTree(page); main.append(tree.copy); scrolls.push(...tree.scrolls);
    }
    visual.append(main);
    visual.classList.add('route-ghost');
    visual.setAttribute('aria-hidden', 'true');
    visual.inert = true;
    return {element: visual, scrolls};
  }

  function mountVisual(visual) {
    const live = shell(), rect = live.getBoundingClientRect();
    ghost = visual.element;
    ghost.style.cssText = `position:fixed;left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;max-width:none;margin:0;pointer-events:none;`;
    document.body.append(ghost);
    for (const [node, top, left] of visual.scrolls) {node.scrollTop = top; node.scrollLeft = left;}
    return ghost;
  }

  function cloneVisual(visual) {
    if (!visual) return null;
    const tree = copyTree(visual.element);
    // Detached scrollable nodes report zero; transfer the remembered offsets.
    const originals = [visual.element, ...visual.element.querySelectorAll('*')];
    const copies = [tree.copy, ...tree.copy.querySelectorAll('*')];
    return {element: tree.copy, scrolls: visual.scrolls.map(([node, top, left]) => [copies[originals.indexOf(node)], top, left]).filter(entry => entry[0])};
  }

  async function route(visual, direction = 'forward') {
    if (!visual || reduced()) {cleanRoute(); return Promise.resolve();}
    cleanRoute();
    const ticket = routeTicket, live = shell(), old = mountVisual(visual);
    const forward = direction === 'forward';
    const duration = forward ? 380 : 330;
    live.classList.toggle('route-moving', forward);
    old.classList.toggle('route-moving', !forward);
    live.style.willChange = 'transform';
    live.style.zIndex = forward ? '202' : '200';
    old.style.zIndex = forward ? '200' : '202';
    const liveFrames = forward ? [{transform: 'translate3d(100%,0,0)'}, {transform: 'translate3d(0,0,0)'}] : [{transform: 'translate3d(-24%,0,0)'}, {transform: 'translate3d(0,0,0)'}];
    const oldFrames = forward ? [{transform: 'translate3d(0,0,0)'}, {transform: 'translate3d(-24%,0,0)'}] : [{transform: 'translate3d(0,0,0)'}, {transform: 'translate3d(100%,0,0)'}];
    routePreparing = true;
    live.style.transform = liveFrames[0].transform;
    old.style.transform = oldFrames[0].transform;
    // Allow initial layout before the transform starts. The cold first-paint
    // stall is tracked separately; these frames do not prove GPU raster readiness.
    await frame(); await frame();
    if (ticket !== routeTicket) return;
    routePreparing = false;
    routeAnimations = [live.animate(liveFrames, {duration, easing: routeEase, fill: 'both'}), old.animate(oldFrames, {duration, easing: routeEase, fill: 'both'})];
    return Promise.all(routeAnimations.map(done)).then(() => {if (ticket === routeTicket) cleanRoute();});
  }

  function reveal(element, {user = false} = {}) {
    if (!element || reduced()) return;
    element.animate([
      {opacity: 0, transform: `translate3d(0,${user ? 9 : 14}px,0) scale(${user ? '.98' : '1'})`},
      {opacity: 1, transform: 'translate3d(0,0,0) scale(1)'}
    ], {duration: user ? 240 : 300, easing: ease});
  }

  async function openSheet(previousHeight = 0) {
    const dialog = document.getElementById('sheet'), wasOpen = dialog.open;
    sheetTicket++; sheetAnimation?.cancel(); sheetClosing = false;
    dialog.style.removeProperty('transform');
    dialog.style.removeProperty('will-change');
    dialog.classList.remove('sheet-leaving', 'sheet-dragging', 'sheet-preparing');
    if (!wasOpen) dialog.showModal();
    if (reduced()) return;
    if (!wasOpen) {
      const ticket = sheetTicket;
      dialog.classList.add('sheet-preparing');
      dialog.style.transform = 'translate3d(0,100%,0)';
      dialog.style.willChange = 'transform';
      await frame(); await frame();
      if (ticket !== sheetTicket || !dialog.open) return;
      dialog.classList.remove('sheet-preparing');
      dialog.classList.remove('sheet-entering'); void dialog.offsetHeight;
      dialog.classList.add('sheet-entering');
      sheetAnimation = dialog.animate([{transform: 'translate3d(0,100%,0)'}, {transform: 'translate3d(0,0,0)'}], {duration: 390, easing: settle, fill: 'both'});
      await done(sheetAnimation);
      if (ticket === sheetTicket) {dialog.style.removeProperty('transform');dialog.style.removeProperty('will-change');sheetAnimation?.cancel();}
    } else {
      const body = document.getElementById('sheetBody');
      body.animate([{opacity: .3, transform: 'translate3d(0,8px,0)'}, {opacity: 1, transform: 'translate3d(0,0,0)'}], {duration: 180, easing: ease});
    }
  }

  async function closeSheet(onClosed) {
    const dialog = document.getElementById('sheet');
    if (!dialog.open || sheetClosing) return;
    if (dialog.classList.contains('sheet-preparing')) {
      sheetTicket++; sheetAnimation?.cancel();dialog.close();dialog.classList.remove('sheet-preparing');dialog.style.removeProperty('transform');dialog.style.removeProperty('will-change');onClosed?.();return;
    }
    sheetClosing = true;
    const ticket = ++sheetTicket;
    const start = getComputedStyle(dialog).transform;
    sheetAnimation?.cancel();
    dialog.classList.remove('sheet-entering');
    dialog.classList.add('sheet-leaving');
    if (!reduced()) {
      sheetAnimation = dialog.animate([{transform: start === 'none' ? 'translate3d(0,0,0)' : start}, {transform: `translate3d(0,${dialog.offsetHeight + 30}px,0)`}], {duration: 230, easing: 'cubic-bezier(.4,0,1,1)', fill: 'forwards'});
      await done(sheetAnimation);
    }
    if (ticket !== sheetTicket) return;
    dialog.close();
    sheetAnimation?.cancel();
    dialog.style.removeProperty('transform');
    dialog.classList.remove('sheet-leaving', 'sheet-dragging');
    sheetClosing = false; sheetAnimation = null;
    onClosed?.();
  }

  function installSheetGesture() {
    const dialog = document.getElementById('sheet');
    dialog.addEventListener('cancel', event => {event.preventDefault(); closeSheet();});
    dialog.addEventListener('pointerdown', event => {
      if (sheetClosing || !event.isPrimary || !event.target.closest('.sheet-header,.sheet-handle') || event.target.closest('button')) return;
      sheetAnimation?.cancel();
      sheetDrag = {id: event.pointerId, y: event.clientY, lastY: event.clientY, time: performance.now(), velocity: 0, distance: 0};
      dialog.setPointerCapture(event.pointerId);
    });
    dialog.addEventListener('pointermove', event => {
      if (!sheetDrag || event.pointerId !== sheetDrag.id) return;
      const now = performance.now();
      sheetDrag.distance = Math.max(0, event.clientY - sheetDrag.y);
      sheetDrag.velocity = (event.clientY - sheetDrag.lastY) / Math.max(1, now - sheetDrag.time);
      sheetDrag.lastY = event.clientY; sheetDrag.time = now;
      dialog.classList.add('sheet-dragging');
      dialog.style.transform = `translate3d(0,${sheetDrag.distance}px,0)`;
      event.preventDefault();
    }, {passive: false});
    const finish = event => {
      if (!sheetDrag || event.pointerId !== sheetDrag.id) return;
      const drag = sheetDrag; sheetDrag = null;
      const dismiss = event.type !== 'pointercancel' && (drag.distance > Math.min(120, dialog.offsetHeight * .25) || drag.distance > 35 && drag.velocity > .65);
      if (dismiss) {feedback(); closeSheet();}
      else {
        const start = dialog.style.transform;
        dialog.style.removeProperty('transform');
        if (!reduced()) sheetAnimation = dialog.animate([{transform: start}, {transform: 'translate3d(0,0,0)'}], {duration: 300, easing: settle});
      }
      dialog.classList.remove('sheet-dragging');
    };
    dialog.addEventListener('pointerup', finish);
    dialog.addEventListener('pointercancel', finish);
  }

  function installBackGesture({canStart, previous, commit}) {
    const live = shell(); let gesture = null;
    live.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.clientX > 24 || !canStart() || reduced() || event.target.closest('input,textarea,select')) return;
      gesture = {id: event.pointerId, x: event.clientX, y: event.clientY, lastX: event.clientX, lastTime: performance.now(), distance: 0, velocity: 0, active: false};
    });
    live.addEventListener('pointermove', event => {
      if (!gesture || gesture.id !== event.pointerId) return;
      const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
      if (!gesture.active) {
        if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) {gesture = null; return;}
        if (dx < 9 || dx < Math.abs(dy) * 1.2) return;
        const visual = cloneVisual(previous());
        if (!visual) {gesture = null; return;}
        cleanRoute();
        mountVisual(visual).style.zIndex = '200';
        live.style.zIndex = '202'; live.classList.add('route-moving');
        live.style.willChange = 'transform';
        gesture.active = true; draggingBack = true;
        live.setPointerCapture(event.pointerId);
      }
      const now = performance.now(), width = live.offsetWidth;
      gesture.velocity = (event.clientX - gesture.lastX) / Math.max(1, now - gesture.lastTime);
      gesture.lastX = event.clientX; gesture.lastTime = now;
      gesture.distance = Math.max(0, Math.min(width, dx));
      live.style.transform = `translate3d(${gesture.distance}px,0,0)`;
      ghost.style.transform = `translate3d(${-width * .24 * (1 - gesture.distance / width)}px,0,0)`;
      event.preventDefault();
    }, {passive: false});
    const finish = async event => {
      if (!gesture || gesture.id !== event.pointerId) return;
      const g = gesture; gesture = null;
      if (!g.active) return;
      suppressClickUntil = performance.now() + 400;
      const width = live.offsetWidth;
      const accept = event.type !== 'pointercancel' && (g.distance > width * .32 || g.distance > 42 && g.velocity > .55);
      const duration = accept ? Math.min(280, Math.max(150, (width - g.distance) * .65)) : 270;
      const ticket = routeTicket;
      routeAnimations = [
        live.animate([{transform: `translate3d(${g.distance}px,0,0)`}, {transform: `translate3d(${accept ? width : 0}px,0,0)`}], {duration, easing: settle, fill: 'forwards'}),
        ghost.animate([{transform: ghost.style.transform}, {transform: `translate3d(${accept ? 0 : -width * .24}px,0,0)`}], {duration, easing: settle, fill: 'forwards'})
      ];
      await Promise.all(routeAnimations.map(done));
      if (ticket !== routeTicket) return;
      cleanRoute(); draggingBack = false;
      if (accept) {feedback(); commit();}
    };
    live.addEventListener('pointerup', finish);
    live.addEventListener('pointercancel', finish);
    document.addEventListener('click', event => {
      if (performance.now() < suppressClickUntil) {event.preventDefault(); event.stopImmediatePropagation();}
    }, true);
  }

  // Android 14+ owns the edge gesture. Its progress/cancel callbacks drive the
  // same surfaces instead of competing with the OS's gesture recognizer.
  function systemBackStart() {
    if (!backOptions?.canStart() || reduced()) return;
    const visual = cloneVisual(backOptions.previous());
    if (!visual) return;
    cleanRoute();
    const live = shell(); mountVisual(visual).style.zIndex = '200';
    live.style.zIndex = '202'; live.style.willChange = 'transform'; live.classList.add('route-moving');
    systemBack = {progress: 0, ticket: routeTicket}; draggingBack = true;
    systemBackProgress(0);
  }

  function systemBackProgress(progress) {
    if (!systemBack) return;
    const p = Math.max(0, Math.min(1, progress)), width = shell().offsetWidth;
    systemBack.progress = p;
    shell().style.transform = `translate3d(${width * p}px,0,0)`;
    ghost.style.transform = `translate3d(${-width * .24 * (1 - p)}px,0,0)`;
  }

  function systemBackFinish(accept) {
    if (!systemBack) return false;
    const session = systemBack, live = shell(), width = live.offsetWidth;
    const duration = reduced() ? 0 : accept ? 220 : 270;
    const animations = [
      live.animate([{transform: live.style.transform}, {transform: `translate3d(${accept ? width : 0}px,0,0)`}], {duration,easing:settle,fill:'forwards'}),
      ghost.animate([{transform: ghost.style.transform}, {transform: `translate3d(${accept ? 0 : -width * .24}px,0,0)`}], {duration,easing:settle,fill:'forwards'})
    ];
    routeAnimations = animations;
    Promise.all(animations.map(done)).then(() => {
      if (session.ticket !== routeTicket) return;
      cleanRoute();
      if (accept) backOptions.commit();
    });
    return true;
  }

  function configure(getPreferences, triggerHaptic) {preferences = getPreferences; haptic = triggerHaptic;}
  function install(options) {
    backOptions = options;
    installSheetGesture(); installBackGesture(options);
    document.addEventListener('pointerup', event => {
      if (event.target.closest('button:not(:disabled)') && !draggingBack) feedback();
    }, {passive: true});
    reduceQuery.addEventListener('change', () => {if (reduced()) cleanRoute();});
  }
  return {configure, install, capture, route, reveal, openSheet, closeSheet, reduced, feedback, cancel: cleanRoute, systemBackStart, systemBackProgress, systemBackFinish,
    get sheetClosing() {return sheetClosing;}, get navigating() {return routePreparing || routeAnimations.length > 0 || draggingBack;}};
})();
