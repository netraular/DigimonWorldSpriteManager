"use strict";
/* Shared card navigation: a card is a <div>, so the browser gives it none of an
 * <a>'s open-in-a-new-tab behaviour. bindOpen puts it back — middle click (and
 * ctrl/⌘/shift click) opens the card's URL in another tab, a plain click runs
 * the in-page handler. Used by the Animate, Crop and Preview galleries.
 *
 *   bindOpen(el, urlFor, activate)
 *     urlFor(e)  -> the URL this click targets, or null to ignore the click
 *                   (quick-action buttons, real <a> children…)
 *     activate(e) -> what a plain left click does instead of navigating
 */
function bindOpen(el, urlFor, activate) {
  const urlOf = (e) => (typeof urlFor === "function" ? urlFor(e) : urlFor);
  el.addEventListener("click", (e) => {
    const url = urlOf(e);
    if (url && (e.ctrlKey || e.metaKey || e.shiftKey)) { window.open(url, "_blank"); return; }
    activate(e);
  });
  // Middle click: mousedown must be swallowed too, or the browser starts its
  // autoscroll instead of letting auxclick through.
  el.addEventListener("mousedown", (e) => { if (e.button === 1 && urlOf(e)) e.preventDefault(); });
  el.addEventListener("auxclick", (e) => {
    if (e.button !== 1) return;
    const url = urlOf(e);
    if (!url) return;
    e.preventDefault();
    window.open(url, "_blank");
  });
}
