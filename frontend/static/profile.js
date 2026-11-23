/* Profile, account settings and password recovery.
   Loaded after app.js and reuses its globals: api, toast, escapeHtml, user, csrfToken,
   workspace, navUser, renderDashboard, showLogin. */
(() => {
  const initials = (n) => String(n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  const avatar = (p, size) => p.avatar_url
    ? `<img class="avatar ${size}" src="${escapeHtml(p.avatar_url)}" alt="Profile picture">`
    : `<span class="avatar ${size}" aria-hidden="true">${escapeHtml(initials(p.name))}</span>`;
  const kv = (k, v) => `<div class="kv"><span>${k}</span><strong>${escapeHtml(v || "Not set")}</strong></div>`;

  async function uploadAvatar(file) {
    const fd = new FormData();
    fd.append("avatar", file);
    const res = await fetch("/api/profile/avatar", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": csrfToken }, body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Upload failed.");
    return data;
  }

  async function showProfile() {
    document.querySelector(".hero").hidden = true; workspace.hidden = false;
    let p;
    try { p = (await api("/api/profile")).profile; } catch (e) { toast(e.message); return; }
    const isStudent = p.role === "Student", isTeacher = p.role === "Teacher";
    workspace.innerHTML = `
      <section class="panel">
        <div class="toolbar">
          <div class="profile-head">${avatar(p, "lg")}<div><p class="eyebrow">${escapeHtml(p.role)}</p><h2>${escapeHtml(p.name)}</h2><p class="muted">${escapeHtml(p.login_id)}</p></div></div>
          <div class="tabs"><button class="secondary" id="pf-back">Back to dashboard</button><button class="secondary" id="pf-logout">Log out</button></div>
        </div>
        <div class="profile-grid">
          <form class="card" id="pf-info">
            <h3>Personal information</h3>
            ${isTeacher ? `<label class="field">Full name<input name="name" value="${escapeHtml(p.name)}" required maxlength="120"></label>` : ""}
            <label class="field">Email address<input name="email" type="email" value="${escapeHtml(p.email || "")}" required maxlength="120"></label>
            <label class="field">Contact number<input name="contact" value="${escapeHtml(p.contact || "")}" maxlength="40" placeholder="e.g. 0917 123 4567"></label>
            <button class="primary" type="submit">Save changes</button>
          </form>
          <div class="card">
            <h3>Profile picture</h3>
            <div class="profile-head">${avatar(p, "md")}<p class="muted">PNG, JPEG or WebP, up to 2 MB.</p></div>
            <label class="field">Choose image<input type="file" id="pf-file" accept="image/png,image/jpeg,image/webp"></label>
            <div class="actions"><button class="primary" id="pf-upload" type="button">Upload</button>${p.avatar_url ? '<button class="danger" id="pf-remove" type="button">Remove</button>' : ""}</div>
          </div>
          ${isStudent ? `<div class="card"><h3>Academic information</h3>${kv("Student ID", p.student_id)}${kv("Program", p.course)}${kv("Year level", p.year_level ? `${p.year_level}` : "")}${kv("Section", p.section)}${kv("Status", p.status)}<p class="muted">Contact an administrator to correct school records.</p></div>` : ""}
          <form class="card" id="pf-pass">
            <h3>Change password</h3>
            <label class="field">Current password<input name="current" type="password" required autocomplete="current-password"></label>
            <label class="field">New password (8+ characters)<input name="next" type="password" required minlength="8" autocomplete="new-password"></label>
            <label class="field">Confirm new password<input name="confirm" type="password" required minlength="8" autocomplete="new-password"></label>
            <button class="primary" type="submit">Update password</button>
          </form>
          <div class="card"><h3>Account</h3>${kv("Login ID", p.login_id)}${kv("Role", p.role)}${kv("Email", p.email)}<p class="muted">Forgot your password later? Use "Forgot password?" on the login page.</p></div>
        </div>
      </section>`;

    document.querySelector("#pf-back").onclick = () => renderDashboard();
    document.querySelector("#pf-logout").onclick = () => signOut();
    document.querySelector("#pf-info").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.currentTarget);
      const body = { email: f.get("email"), contact: f.get("contact") };
      if (isTeacher) body.name = f.get("name");
      try {
        const d = await api("/api/profile", { method: "PUT", body: JSON.stringify(body) });
        if (isTeacher && user) user.teacher_name = d.profile.name;
        toast("Profile saved."); showProfile();
      } catch (err) { toast(err.message); }
    };
    document.querySelector("#pf-upload").onclick = async () => {
      const file = document.querySelector("#pf-file").files[0];
      if (!file) return toast("Choose an image first.");
      if (file.size > 2 * 1024 * 1024) return toast("Image is too large (2 MB max).");
      try { await uploadAvatar(file); toast("Profile picture updated."); showProfile(); } catch (err) { toast(err.message); }
    };
    const rm = document.querySelector("#pf-remove");
    if (rm) rm.onclick = async () => {
      if (!confirm("Remove your profile picture?")) return;
      try { await api("/api/profile/avatar", { method: "DELETE" }); toast("Profile picture removed."); showProfile(); } catch (err) { toast(err.message); }
    };
    document.querySelector("#pf-pass").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.currentTarget);
      if (f.get("next") !== f.get("confirm")) return toast("New passwords do not match.");
      try {
        await api("/api/account/password", { method: "POST", body: JSON.stringify({ current_password: f.get("current"), new_password: f.get("next") }) });
        e.currentTarget.reset(); toast("Password changed.");
      } catch (err) { toast(err.message); }
    };
  }

  function showForgot() {
    document.querySelector(".hero").hidden = true; workspace.hidden = false;
    workspace.innerHTML = `<form class="login" id="forgot-form"><h2>Forgot password</h2>
      <p class="muted">Enter your student ID or email. If the account has an email address, we will send a reset link that expires in 30 minutes.</p>
      <label class="field">Email / ID<input name="identity" required autocomplete="username"></label>
      <button class="primary" type="submit">Send reset link</button>
      <a href="#" class="muted-link" id="back-login">Back to login</a></form>`;
    document.querySelector("#back-login").onclick = (e) => { e.preventDefault(); showLogin(); };
    document.querySelector("#forgot-form").onsubmit = async (e) => {
      e.preventDefault();
      try {
        const d = await api("/api/auth/forgot", { method: "POST", body: JSON.stringify({ identity: new FormData(e.currentTarget).get("identity") }) });
        toast(d.message);
      } catch (err) { toast(err.message); }
    };
  }

  function showReset(token) {
    document.querySelector(".hero").hidden = true; workspace.hidden = false;
    workspace.innerHTML = `<form class="login" id="reset-form"><h2>Choose a new password</h2>
      <label class="field">New password (8+ characters)<input name="next" type="password" required minlength="8" autocomplete="new-password"></label>
      <label class="field">Confirm new password<input name="confirm" type="password" required minlength="8" autocomplete="new-password"></label>
      <button class="primary" type="submit">Reset password</button></form>`;
    document.querySelector("#reset-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.currentTarget);
      if (f.get("next") !== f.get("confirm")) return toast("Passwords do not match.");
      try {
        const d = await api("/api/auth/reset", { method: "POST", body: JSON.stringify({ token, new_password: f.get("next") }) });
        history.replaceState(null, "", "/"); toast(d.message); showLogin();
      } catch (err) { toast(err.message); }
    };
  }

  // ---- Front page (landing) behaviour -------------------------------------
  function showLanding() {
    document.querySelector(".hero").hidden = false;
    workspace.hidden = true;
    document.querySelector("#start-login").textContent = user ? "Go to dashboard" : "Access GRATTEN";
    window.scrollTo({ top: 0 });
  }

  async function signOut() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
    user = null;
    navUser.innerHTML = '<button type="button" class="nav-login" id="nav-login">Log in</button>';
    document.querySelector("#nav-login").onclick = () => showLogin();
    workspace.innerHTML = "";
    showLanding();
  }

  function drawSignedInNav() {
    navUser.innerHTML = `<span>${escapeHtml(user.name || user.teacher_name || user.login_id)} (${escapeHtml(user.role)})</span> <button id="logout">Logout</button>`;
  }

  // "Access GRATTEN" opens the dashboard when already signed in, the login form otherwise.
  document.querySelector("#start-login").onclick = () => (user ? renderDashboard() : showLogin());

  // The logo, Home and Features links stay on the front page instead of reloading into the dashboard.
  document.querySelectorAll('.brand, .nav-links a[href="/"]').forEach((a) =>
    a.addEventListener("click", (e) => { e.preventDefault(); showLanding(); }));
  document.querySelectorAll('.nav-links a[href="#features"]').forEach((a) =>
    a.addEventListener("click", (e) => { e.preventDefault(); showLanding(); document.querySelector("#features").scrollIntoView({ behavior: "smooth" }); }));

  // app.js announces an existing session on page load; show the front page, not the dashboard.
  document.addEventListener("gratten:signed-in", () => { drawSignedInNav(); showLanding(); });
  if (user) { drawSignedInNav(); showLanding(); }

  // Whenever the signed-in nav is drawn, add Dashboard + Profile buttons and a Logout that returns to the front page.
  new MutationObserver(() => {
    const out = document.querySelector("#logout");
    if (out && !document.querySelector("#nav-profile")) {
      const d = document.createElement("button");
      d.id = "nav-dashboard"; d.type = "button"; d.textContent = "Dashboard"; d.onclick = () => renderDashboard();
      const b = document.createElement("button");
      b.id = "nav-profile"; b.type = "button"; b.textContent = "Profile"; b.onclick = showProfile;
      out.before(d, b);
      out.onclick = signOut;
    }
  }).observe(navUser, { childList: true });

  // Add "Forgot password?" under the login form whenever app.js draws it.
  new MutationObserver(() => {
    const f = document.querySelector("#login-form");
    if (f && !f.querySelector("#forgot-link")) {
      const a = document.createElement("a");
      a.href = "#"; a.id = "forgot-link"; a.className = "muted-link"; a.textContent = "Forgot password?";
      a.onclick = (e) => { e.preventDefault(); showForgot(); };
      f.append(a);
    }
  }).observe(workspace, { childList: true });

  // Opened from an emailed link: /?reset=TOKEN
  const token = new URLSearchParams(location.search).get("reset");
  if (token) Promise.allSettled([api("/api/csrf"), api("/api/auth/me")]).then(() => setTimeout(() => showReset(token), 400));
})();
