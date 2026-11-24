/* Solo dev page: sandboxed single-user journal (no partner). */

var status = document.getElementById("solo-status");
var current = null;

function drop(id, keepalive) {
  if (!id) return Promise.resolve();
  return fetch("/api/v1/sandbox/" + id, { method: "DELETE", keepalive: !!keepalive })
    .catch(function () {});
}

function load(id, secret) {
  var frame = document.getElementById("solo-frame");
  frame.src = "/?profile=solo&sandbox=" + id + "&token=" + encodeURIComponent(secret);
}

function create() {
  status.textContent = "creating…";
  return fetch("/api/v1/sandbox/create?mode=solo", { method: "POST" })
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (sb) {
      current = sb.sandbox_id;
      status.textContent = "(" + current.slice(0, 8) + ")";
      load(current, sb.alice_token);
    })
    .catch(function (err) {
      status.textContent = "failed: " + err.message;
    });
}

document.getElementById("solo-reset").addEventListener("click", function () {
  var old = current;
  current = null;
  drop(old).then(create);
});

window.addEventListener("pagehide", function () { drop(current, true); });

create();
