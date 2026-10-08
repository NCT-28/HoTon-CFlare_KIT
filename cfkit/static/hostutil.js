"use strict";
// Pure helpers for the "default domain" feature. Loaded in the browser (window.HostUtil) and by node tests.
(function (root) {
  function normalizeDomain(d) {
    return String(d || "").trim().toLowerCase().replace(/^\.+|\.+$/g, "");
  }

  // sub-domain typed by the user -> full hostname. "@" or the domain itself = the domain apex.
  function toHost(sub, domain) {
    const s = String(sub || "").trim().toLowerCase();
    const dom = normalizeDomain(domain);
    if (!dom || s === "") return s;
    if (s === "@" || s === dom) return dom;
    if (s.endsWith("." + dom)) return s;
    return s + "." + dom;
  }

  // full hostname -> text shown in the sub-domain input; null when the host is not under the domain.
  function subOf(host, domain) {
    const dom = normalizeDomain(domain);
    if (!dom) return null;
    if (host === "") return "";
    if (host === dom) return "@";
    if (host.endsWith("." + dom)) return host.slice(0, host.length - dom.length - 1);
    return null;
  }

  const api = { normalizeDomain, toHost, subOf };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.HostUtil = api;
})(this);
