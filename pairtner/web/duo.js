/* Duo dev page: create an isolated sandbox DB, point both iframes at it. */

var banner = document.getElementById("duo-id");
var current = null;

function drop(id, keepalive) {
  if (!id) { return Promise.resolve(); }
  return fetch("/api/v1/sandbox/" + id, { method: "DELETE", keepalive: !!keepalive })
    .catch(function () {});
}

function load(frame, id, secret) {
  frame.src = "/?" + frame.getAttribute("data-query") + "&sandbox=" + id +
    "&token=" + encodeURIComponent(secret);
}

function create() {
  banner.textContent = "creating…";
  return fetch("/api/v1/sandbox/create", { method: "POST" }).then(function (res) {
    if (!res.ok) { throw new Error("HTTP " + res.status); }
    return res.json();
  }).then(function (sb) {
    current = sb.sandbox_id;
    banner.textContent = "(" + current.slice(0, 8) + ")";
    load(document.getElementById("duo-alice"), current, sb.alice_token);
    load(document.getElementById("duo-bob"), current, sb.bob_token);
  }).catch(function (err) {
    banner.textContent = "failed: " + err.message;
  });
}

document.getElementById("duo-reset").addEventListener("click", function () {
  var old = current;
  current = null;
  drop(old).then(create);
});

window.addEventListener("pagehide", function () { drop(current, true); });

create();
