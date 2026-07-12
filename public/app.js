const state = {
  games: [],
  players: [],
  currentGameId: null,
  dashboard: null,
  filters: {},
  missingPlayers: new Set(),
  missingStrict: false,
  pinned: new Set(),
  adminSecret: "",
  adminVerified: false,
};

const $ = (selector) => document.querySelector(selector);
const ADMIN_SESSION_KEY = "achievementTrackerAdminSecret";
const ADMIN_SESSION_TTL_MS = 60 * 60 * 1000;

function adminSecret() {
  return state.adminSecret;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (options.admin) headers["X-Admin-Secret"] = adminSecret();
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

// Shareable-link state: current game (by Steam app id) and pinned achievement keys
// are encoded into the URL query string so a link fully reproduces a selection.
function readUrlState() {
  const params = new URLSearchParams(location.search);
  return {
    appId: params.get("game") || "",
    pins: (params.get("pins") || "").split(",").map((s) => s.trim()).filter(Boolean),
  };
}

function syncUrl() {
  const game = state.games.find((g) => g.id === state.currentGameId);
  const params = new URLSearchParams();
  if (game) params.set("game", String(game.app_id));
  if (state.pinned.size) params.set("pins", [...state.pinned].join(","));
  const query = params.toString();
  history.replaceState(null, "", query ? `${location.pathname}?${query}` : location.pathname);
}

async function loadConfig() {
  await restoreAdminSecret();
  const [games, players, plugins, health] = await Promise.all([
    api("/api/games"),
    api("/api/players"),
    api("/api/plugins"),
    api("/api/health"),
  ]);
  state.games = games.games;
  state.players = players.players;
  renderGameSelect();
  renderAdminLists();
  renderPluginOptions(plugins.plugins);
  if (health.admin_secret_is_default) {
    toast("ADMIN_SECRET is still the default value: change-me");
  }
  const urlState = readUrlState();
  if (urlState.appId) {
    const matched = state.games.find((g) => String(g.app_id) === urlState.appId);
    if (matched) {
      state.currentGameId = matched.id;
      state.pinned = new Set(urlState.pins);
    }
  }
  if (!state.currentGameId && state.games.length) {
    state.currentGameId = state.games[0].id;
  }
  syncUrl();
  if (state.currentGameId) await loadDashboard();
  render();
}

function setAdminVerified(verified, secret = "") {
  state.adminVerified = verified;
  state.adminSecret = verified ? secret : "";
  $("#adminContent").hidden = !verified;
  if (verified) {
    $("#adminSecret").value = secret;
    sessionStorage.setItem(
      ADMIN_SESSION_KEY,
      JSON.stringify({ secret, expiresAt: Date.now() + ADMIN_SESSION_TTL_MS }),
    );
  } else {
    sessionStorage.removeItem(ADMIN_SESSION_KEY);
  }
}

async function restoreAdminSecret() {
  const raw = sessionStorage.getItem(ADMIN_SESSION_KEY);
  if (!raw) return;
  try {
    const saved = JSON.parse(raw);
    if (!saved.secret || Number(saved.expiresAt) <= Date.now()) {
      setAdminVerified(false);
      return;
    }
    state.adminSecret = saved.secret;
    await api("/api/admin/verify", { method: "POST", admin: true });
    setAdminVerified(true, saved.secret);
  } catch {
    setAdminVerified(false);
  }
}

async function loadDashboard(refresh = true) {
  if (!state.currentGameId) return;
  try {
    state.dashboard = await api(`/api/games/${state.currentGameId}/dashboard?refresh=${refresh ? "true" : "false"}`);
    state.players = state.dashboard.players;
    renderPlayerFilter();
    renderPluginFilters();
    render();
  } catch (err) {
    // Check if it's a private profile error
    if (err.message && err.message.includes("private")) {
      toast("Error: " + err.message, true);
    }
    throw err;
  }
}

function renderGameSelect() {
  const select = $("#gameSelect");
  select.innerHTML = "";
  if (!state.games.length) {
    select.innerHTML = "<option>No games configured</option>";
    return;
  }
  for (const game of state.games) {
    const option = document.createElement("option");
    option.value = game.id;
    option.textContent = game.name;
    option.selected = game.id === state.currentGameId;
    select.append(option);
  }
}

function renderPlayerFilter() {
  const box = $("#missingPillsBox");
  const input = $("#missingPlayerSearch");

  for (const el of [...box.children]) {
    if (el !== input) el.remove();
  }

  for (const id of state.missingPlayers) {
    const player = state.players.find((p) => String(p.id) === id);
    if (!player) continue;
    const pill = document.createElement("span");
    pill.className = "player-pill";
    const avatarHtml = player.avatar_url ? `<img src="${escapeHtml(player.avatar_url)}" alt="">` : "";
    pill.innerHTML = `${avatarHtml}${escapeHtml(player.display_name)}<button type="button" aria-label="Remove">×</button>`;
    pill.querySelector("button").addEventListener("click", (e) => {
      e.stopPropagation();
      state.missingPlayers.delete(id);
      renderPlayerFilter();
      render();
    });
    box.insertBefore(pill, input);
  }

  input.placeholder = state.missingPlayers.size ? "" : "Any player";

  const dropdown = $("#missingPlayerDropdown");
  const query = input.value.trim().toLowerCase();
  const unselected = state.players.filter((p) => !state.missingPlayers.has(String(p.id)));
  const visible = query ? unselected.filter((p) => p.display_name.toLowerCase().includes(query)) : unselected;

  dropdown.innerHTML = "";
  for (const player of visible) {
    const li = document.createElement("li");
    li.className = "pill-option";
    const avatarHtml = player.avatar_url
      ? `<img src="${escapeHtml(player.avatar_url)}" alt="">`
      : `<span class="pill-option-initials">${escapeHtml(player.display_name.slice(0, 2).toUpperCase())}</span>`;
    li.innerHTML = `${avatarHtml}${escapeHtml(player.display_name)}`;
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      state.missingPlayers.add(String(player.id));
      input.value = "";
      renderPlayerFilter();
      render();
    });
    dropdown.append(li);
  }

  const isFocused = document.activeElement === input;
  dropdown.hidden = !isFocused || visible.length === 0;
}

function renderPluginOptions(plugins) {
  const select = document.querySelector('#gameForm select[name="plugin"]');
  select.innerHTML = '<option value="">No plugin</option>';
  for (const plugin of plugins) {
    const option = document.createElement("option");
    option.value = plugin.slug;
    option.textContent = plugin.label;
    select.append(option);
  }
}

function renderPluginFilters() {
  const root = $("#pluginFilters");
  root.innerHTML = "";
  const fields = state.dashboard?.plugin_fields || [];
  const filterConfig = state.dashboard?.plugin_filter_config || {};
  for (const field of fields) {
    const values = new Set();
    for (const achievement of state.dashboard.achievements) {
      const value = achievement.metadata?.[field.key];
      if (value !== undefined && value !== null) {
        if (Array.isArray(value)) {
          // Plugin returned multiple values for this field (e.g. an achievement tied to several items); add each
          for (const item of value) {
            if (item !== null && item !== undefined) {
              const str = String(item).trim();
              if (str) values.add(str);
            }
          }
        } else {
          const str = String(value);
          values.add(str);
        }
      }
    }
    values.delete("");
    const config = filterConfig[field.key] || {};
    const order = config.order || "alpha";

    // Use explicit option list from plugin if provided, otherwise collect from achievements.
    const sourceValues = config.options ? config.options.map(String) : [...values];
    let sortedValues;
    if (Array.isArray(order)) {
      sortedValues = [...sourceValues].sort((a, b) => {
        const ia = order.indexOf(a);
        const ib = order.indexOf(b);
        if (ia === -1 && ib === -1) return a.localeCompare(b);
        if (ia === -1) return 1;
        if (ib === -1) return -1;
        return ia - ib;
      });
    } else {
      sortedValues = [...sourceValues].sort((a, b) => a.localeCompare(b));
    }

    const label = document.createElement("label");
    label.textContent = field.label;
    const select = document.createElement("select");
    select.dataset.pluginKey = field.key;
    select.innerHTML = `<option value="all">All ${field.label.toLowerCase()}</option>`;
    const noneOption = document.createElement("option");
    noneOption.value = "__none__";
    noneOption.textContent = config.none_label || "(none)";
    select.append(noneOption);
    sortedValues.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.append(option);
    });
    const current = state.filters[field.key];
    select.value = current !== undefined && current !== null ? current : "all";
    select.addEventListener("change", () => {
      state.filters[field.key] = select.value;
      render();
    });
    label.append(select);
    root.append(label);
  }
}

function render() {
  const dashboard = state.dashboard;
  const empty = $("#emptyState");
  const list = $("#achievementList");
  if (!dashboard || !dashboard.achievements.length) {
    empty.style.display = "block";
    list.innerHTML = "";
    $("#achievementCount").textContent = "0";
    $("#completeCount").textContent = "0";
    $("#playerCount").textContent = String(state.players.length);
    return;
  }

  const achievements = filteredAchievements(dashboard.achievements);
  achievements.sort((a, b) => {
    const pa = state.pinned.has(a.api_name) ? 0 : 1;
    const pb = state.pinned.has(b.api_name) ? 0 : 1;
    return pa - pb;
  });
  empty.style.display = achievements.length ? "none" : "block";
  $("#achievementCount").textContent = String(dashboard.achievements.length);
  $("#completeCount").textContent = String(dashboard.achievements.filter((a) => a.missing_count === 0).length);
  $("#playerCount").textContent = String(dashboard.players.length);
  $("#subtitle").textContent = `${dashboard.game.name} - ${achievements.length} visible of ${dashboard.achievements.length}`;

  list.innerHTML = "";
  for (const achievement of achievements) {
    list.append(renderAchievement(achievement));
  }
}

function filteredAchievements(achievements) {
  const search = $("#searchInput").value.trim().toLowerCase();
  const status = $("#statusFilter").value;
  const filterConfig = state.dashboard?.plugin_filter_config || {};
  return achievements.filter((achievement) => {
    const haystack = `${achievement.display_name} ${achievement.description}`.toLowerCase();
    if (search && !haystack.includes(search)) return false;
    if (status === "missing" && achievement.missing_count === 0) return false;
    if (status === "complete" && achievement.missing_count !== 0) return false;
    if (status === "none" && achievement.achieved_count !== 0) return false;
    if (state.missingPlayers.size) {
      for (const player of achievement.players) {
        const isSelected = state.missingPlayers.has(String(player.player_id));
        if (isSelected && player.achieved) return false;
        if (!isSelected && state.missingStrict && !player.achieved) return false;
      }
    }
    for (const [key, value] of Object.entries(state.filters)) {
      if (value === "all") continue;
      {
        const metaVal = achievement.metadata?.[key];
        const config = filterConfig[key] || {};
        const filterType = config.type || "exact";
        let matches = false;

        if (value === "__none__") {
          matches = !metaVal || (Array.isArray(metaVal) ? metaVal.length === 0 : metaVal === "");
        } else if (filterType === "inclusive" && Array.isArray(config.order)) {
          // Inclusive filter: match if achievement value is at or below selected in order
          const order = config.order;
          const selectedIdx = order.indexOf(value);
          if (selectedIdx !== -1) {
            if (Array.isArray(metaVal)) {
              for (const item of metaVal) {
                const itemIdx = order.indexOf(item);
                if (itemIdx !== -1 && itemIdx <= selectedIdx) {
                  matches = true;
                  break;
                }
              }
            } else {
              const itemIdx = order.indexOf(metaVal ?? "");
              if (itemIdx !== -1 && itemIdx <= selectedIdx) {
                matches = true;
              }
            }
          }
        } else if (filterType === "multi" || Array.isArray(metaVal)) {
          // Multi-value filter: match if any element matches
          const values = Array.isArray(metaVal) ? metaVal : [metaVal];
          for (const item of values) {
            if (String(item) === value) {
              matches = true;
              break;
            }
          }
        } else {
          // Exact match (default)
          const strVal = metaVal ?? "";
          if (strVal === value) matches = true;
        }
        if (!matches) return false;
      }
    }
    return true;
  });
}

function tagLinkOrSpan(field, value) {
  const str = String(value);
  return `<a href="#" class="tag tag-clickable" data-filter-key="${escapeHtml(field.key)}" data-filter-value="${escapeHtml(str)}">${escapeHtml(field.label)}: ${escapeHtml(str)}</a>`;
}

// Generic: apply a plugin filter value and keep its <select> in sync, wherever it was triggered from.
function setPluginFilter(key, value) {
  state.filters[key] = value;
  const select = document.querySelector(`#pluginFilters select[data-plugin-key="${CSS.escape(key)}"]`);
  if (select) select.value = value;
  render();
}

function renderAchievement(achievement) {
  const article = document.createElement("article");
  article.className = "achievement";
  const icon = achievement.icon || achievement.icon_gray || "";
  let tagsHtml = "";
  const meta = achievement.metadata || {};
  const pluginFields = state.dashboard?.plugin_fields || [];
  // Only declared plugin fields become tags; plugins may stash other keys in metadata
  // (e.g. as intermediate data for enrich()) without them leaking into the UI.
  // Generic convention: a field marked "clickable" renders its value(s) as buttons
  // that apply the matching plugin filter on click, instead of static text.
  for (const field of pluginFields) {
    const value = meta[field.key];
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      if (!value.length) continue;
      if (field.clickable) {
        for (const item of value) {
          tagsHtml += tagLinkOrSpan(field, item);
        }
      } else {
        tagsHtml += `<span class="tag">${escapeHtml(field.label)}: ${escapeHtml(value.join(", "))}</span>`;
      }
    } else if (field.clickable) {
      tagsHtml += tagLinkOrSpan(field, value);
    } else {
      tagsHtml += `<span class="tag">${escapeHtml(field.label)}: ${escapeHtml(String(value))}</span>`;
    }
  }
  // Generic convention: any plugin can attach an external-source link via "source_label"
  // (display text) and "source_url" (link target, optional).
  if (meta.source_label) {
    const url = meta.source_url;
    tagsHtml += url
      ? `<a class="tag" href="${escapeHtml(url)}" target="_blank" rel="noopener">Source: ${escapeHtml(String(meta.source_label))}</a>`
      : `<span class="tag">Source: ${escapeHtml(String(meta.source_label))}</span>`;
  }
  const isPinned = state.pinned.has(achievement.api_name);
  article.classList.toggle("pinned", isPinned);
  article.title = isPinned ? "Click to unpin" : "Click to pin to top";
  article.innerHTML = `
    <img src="${escapeHtml(icon)}" alt="">
    <div>
      <h3>${escapeHtml(achievement.display_name)}</h3>
      <p class="description">${escapeHtml(achievement.description || "No description available.")}</p>
      <div class="tags">
        <span class="tag">${achievement.achieved_count}/${achievement.players.length} complete</span>
        ${achievement.hidden ? '<span class="tag">Hidden</span>' : ""}
        <span class="tag">Key: ${escapeHtml(achievement.api_name)}</span>
        ${tagsHtml}
      </div>
    </div>
    <div class="player-grid">
      ${achievement.players
        .map((player) => {
          const avatar = player.avatar_url || "";
          const name = escapeHtml(player.display_name);
          const achieved = player.achieved;
          const statusClass = achieved ? "achieved" : "missing";
          const statusIcon = achieved ? '<span class="status-icon" title="Achieved">✓</span>' : '';
          if (avatar) {
            return `<div class="player-avatar ${statusClass}" title="${name}">
              <img src="${escapeHtml(avatar)}" alt="${name}" loading="lazy">
              ${statusIcon}
            </div>`;
          }
          // Fallback: show initials with colored background
          const initials = name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
          return `<div class="player-avatar ${statusClass} no-avatar" title="${name}">
            <span class="avatar-initials">${escapeHtml(initials)}</span>
            ${statusIcon}
          </div>`;
        })
        .join("")}
    </div>
  `;
  article.querySelectorAll(".tag-clickable").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setPluginFilter(link.dataset.filterKey, link.dataset.filterValue);
    });
  });
  article.addEventListener("click", (event) => {
    if (event.target.closest("a")) return;
    if (state.pinned.has(achievement.api_name)) {
      state.pinned.delete(achievement.api_name);
    } else {
      state.pinned.add(achievement.api_name);
    }
    syncUrl();
    render();
  });
  return article;
}

function renderAdminLists() {
  const games = $("#gameAdminList");
  games.innerHTML = "";
  for (const game of state.games) {
    const row = document.createElement("div");
    row.className = "admin-row";
    row.innerHTML = `<span>${escapeHtml(game.name)} (${game.app_id})</span>`;
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.textContent = "Achievements";
    refresh.addEventListener("click", async () => {
      const result = await api(`/api/games/${game.id}/refresh-schema`, { method: "POST", admin: true });
      if (result.plugin_error) {
        toast(`Steam data refreshed. Plugin metadata failed: ${result.plugin_error}`);
      } else {
        toast(`Achievement data refreshed. Plugin metadata: ${result.plugin_metadata || 0}`);
      }
      await loadDashboard(false);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Delete";
    remove.addEventListener("click", async () => {
      await api(`/api/games/${game.id}`, { method: "DELETE", admin: true });
      state.currentGameId = null;
      await loadConfig();
    });
    row.append(refresh, remove);
    games.append(row);
  }

  const players = $("#playerAdminList");
  players.innerHTML = "";
  for (const player of state.players) {
    const row = document.createElement("div");
    row.className = "admin-row";
    row.innerHTML = `<span>${escapeHtml(player.display_name)} (${player.steam_id})</span>`;
    const spacer = document.createElement("span");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Delete";
    remove.addEventListener("click", async () => {
      await api(`/api/players/${player.id}`, { method: "DELETE", admin: true });
      await loadConfig();
    });
    row.append(spacer, remove);
    players.append(row);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

let toastTimer = null;
function toast(message, persistent = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  if (!persistent) {
    toastTimer = setTimeout(() => el.classList.remove("show"), 4200);
  }
}

$("#gameSelect").addEventListener("change", async (event) => {
  state.currentGameId = Number(event.target.value);
  state.filters = {};
  state.missingPlayers.clear();
  state.pinned.clear();
  syncUrl();
  await loadDashboard();
});

$("#refreshPlayersBtn").addEventListener("click", async () => {
  if (!state.currentGameId) return;
  try {
    await api(`/api/games/${state.currentGameId}/refresh-players`, { method: "POST" });
    toast("Player achievement states refreshed.");
    await loadDashboard(false);
  } catch (err) {
    if (err.message && err.message.includes("private")) {
      toast("Error: " + err.message, true);
    } else {
      toast("Error: " + err.message);
    }
  }
});

$("#searchInput").addEventListener("input", render);
$("#statusFilter").addEventListener("change", render);

$("#missingPlayerFilter").addEventListener("click", () => $("#missingPlayerSearch").focus());

$("#missingPlayerSearch").addEventListener("focus", () => {
  $("#missingPlayerDropdown").hidden = false;
  renderPlayerFilter();
});

$("#missingPlayerSearch").addEventListener("blur", () => {
  setTimeout(() => { $("#missingPlayerDropdown").hidden = true; }, 150);
});

$("#missingPlayerSearch").addEventListener("input", renderPlayerFilter);

$("#missingStrictToggle").addEventListener("change", (event) => {
  state.missingStrict = event.target.checked;
  render();
});
$("#adminToggle").addEventListener("click", () => $("#adminPanel").classList.add("open"));
$("#adminClose").addEventListener("click", () => $("#adminPanel").classList.remove("open"));
$("#saveAdminSecret").addEventListener("click", async () => {
  const secret = $("#adminSecret").value.trim();
  setAdminVerified(false);
  if (!secret) {
    toast("Enter the admin secret first.");
    return;
  }
  state.adminSecret = secret;
  try {
    await api("/api/admin/verify", { method: "POST", admin: true });
    setAdminVerified(true, secret);
    toast("Admin unlocked.");
  } catch (error) {
    setAdminVerified(false);
    toast(error.message);
  }
});

$("#gameForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  await api("/api/games", {
    method: "POST",
    admin: true,
    body: JSON.stringify(Object.fromEntries(form.entries())),
  });
  event.target.reset();
  await loadConfig();
});

$("#playerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  await api("/api/players", {
    method: "POST",
    admin: true,
    body: JSON.stringify(Object.fromEntries(form.entries())),
  });
  event.target.reset();
  await loadConfig();
});

loadConfig().catch((error) => toast(error.message));
