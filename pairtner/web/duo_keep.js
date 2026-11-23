/* Persistent duo: uses a fixed sandbox ID so it survives restarts.
   The server creates it on first use; subsequent loads reuse the same DB. */

var KEEP_ID = "persistent";
var banner = document.querySelector(".duo-banner");

function load(frame, token) {
  frame.src = "/?" + frame.getAttribute("data-query") + "&sandbox=" + KEEP_ID +
    "&token=" + encodeURIComponent(token);
}

function setup() {
  // Try to create; if it already exists, the server returns the existing tokens
  return fetch("/api/v1/sandbox/create?keep=" + KEEP_ID, { method: "POST" })
    .then(function (res) { return res.json(); })
    .then(function (sb) {
      load(document.getElementById("duo-alice"), sb.alice_token);
      load(document.getElementById("duo-bob"), sb.bob_token);
    })
    .catch(function (err) {
      banner.textContent = "Failed: " + err.message;
    });
}

document.getElementById("duo-reset").addEventListener("click", function () {
  if (!confirm("Reset persistent duo DB? All conversation history will be lost.")) return;
  fetch("/api/v1/sandbox/" + KEEP_ID, { method: "DELETE" })
    .then(function () { return setup(); });
});

setup();
