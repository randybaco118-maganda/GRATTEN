const app = document.querySelector("#app");
const workspace = document.querySelector("#workspace");
const navUser = document.querySelector("#nav-user");
let user = null, selectedSubject = "DB101", students = [], csrfToken = "";
let currentRecords = [], currentAssessments = [], currentSections = [], selectedSection = "ALL";

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(method !== "GET" ? { "X-CSRF-Token": csrfToken } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (data.csrf_token) csrfToken = data.csrf_token;
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function toast(message) {
  const el = document.querySelector("#toast");
  el.textContent = message;
  el.style.display = "block";
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => { el.style.display = "none"; }, 2800);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtPct(val) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) {
    return '<span class="incomplete-tag">Incomplete</span>';
  }
  return `${Number(val).toFixed(2).replace(/\.00$/, "")}%`;
}

function fmtFinalGrade(val) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) {
    return '<span class="incomplete-tag">Incomplete</span>';
  }
  return `<strong>${escapeHtml(val)}</strong>`;
}

function showLogin() {
  document.querySelector(".hero").hidden = true;
  workspace.hidden = false;
  workspace.innerHTML = `
    <form class="login" id="login-form">
      <h2>Log in to GRATTEN</h2>
      <p class="muted">Use your student ID or registered email.</p>
      <label class="field">Email / ID<input name="identity" required autocomplete="username"></label>
      <label class="field">Password<input name="password" type="password" required autocomplete="current-password"></label>
      <button class="primary" type="submit">Log in</button>
      <p class="muted">
        Demo student: 2026-8812 / Student123!<br>
        Demo student (RBACO): rbaco25a-0030@olopsc.edu.ph / Student123!<br>
        Demo teacher: teacher@olopsc.edu.ph / Teacher123!
      </p>
    </form>`;
  document.querySelector("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      const d = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ identity: f.get("identity"), password: f.get("password") }),
      });
      user = d.user;
      await renderDashboard();
    } catch (err) {
      toast(err.message);
    }
  });
}

async function renderDashboard() {
  document.querySelector(".hero").hidden = true;
  workspace.hidden = false;
  navUser.innerHTML = `<span>${escapeHtml(user.name || user.teacher_name || user.login_id)} (${escapeHtml(user.role)})</span> <button id="logout">Logout</button>`;
  document.querySelector("#logout").onclick = async () => {
    await api("/api/auth/logout", { method: "POST" });
    user = null;
    navUser.innerHTML = "";
    workspace.hidden = true;
    document.querySelector(".hero").hidden = false;
  };

  const [s, st] = await Promise.all([api("/api/subjects"), api("/api/students")]);
  students = st.students;
  if (!s.subjects.some((x) => x.subject_id === selectedSubject)) {
    selectedSubject = s.subjects[0]?.subject_id;
  }

  workspace.innerHTML = `
    <section class="panel">
      <div class="toolbar">
        <div>
          <p class="eyebrow">Academic Year 2026–2027 · First Semester</p>
          <h2>Academic dashboard</h2>
        </div>
        <div class="tabs">
          <button class="secondary" id="grades-tab">Grades</button>
          <button class="secondary" id="attendance-tab">Attendance</button>
          <button class="secondary" id="results-tab">Results</button>
          ${user.role === "Teacher" ? '<button class="secondary" id="add-subject-tab">+ Add subject</button>' : ""}
        </div>
      </div>
      <div class="field">
        <label for="subject">Subject</label>
        <select id="subject">
          ${s.subjects.map((x) => `<option value="${escapeHtml(x.subject_id)}">${escapeHtml(x.code)} — ${escapeHtml(x.name)}</option>`).join("")}
        </select>
      </div>
      <div id="module"></div>
    </section>`;

  const select = document.querySelector("#subject");
  select.value = selectedSubject;
  select.onchange = () => {
    selectedSubject = select.value;
    selectedSection = "ALL";
    renderModule("grades");
  };
  document.querySelector("#grades-tab").onclick = () => renderModule("grades");
  document.querySelector("#attendance-tab").onclick = () => renderModule("attendance");
  document.querySelector("#results-tab").onclick = () => renderResults();
  const addBtn = document.querySelector("#add-subject-tab");
  if (addBtn) addBtn.onclick = showAddSubject;
  renderModule("grades");
}

function showAddSubject() {
  const host = document.querySelector("#module");
  host.innerHTML = `
    <form id="subject-form" class="panel">
      <h3>Add a subject</h3>
      <label class="field">Subject ID
        <input name="subject_id" placeholder="e.g. SE108" required maxlength="12">
      </label>
      <label class="field">Code
        <input name="code" placeholder="e.g. SENG" required maxlength="10">
      </label>
      <label class="field">Subject name
        <input name="name" placeholder="e.g. Software Engineering" required maxlength="80">
      </label>
      <label class="field">Units
        <input name="units" type="number" min="1" max="6" value="3" required>
      </label>
      <label><input type="checkbox" name="enroll_all" checked> Enroll all students in this subject</label>
      <br><br>
      <div class="actions">
        <button class="primary" type="submit">Create subject</button>
        <button type="button" class="secondary" id="cancel-subject">Cancel</button>
      </div>
    </form>`;

  document.querySelector("#cancel-subject").onclick = () => renderModule("grades");
  document.querySelector("#subject-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = {
      subject_id: f.get("subject_id"),
      code: f.get("code"),
      name: f.get("name"),
      units: Number(f.get("units")),
      enroll_all: f.get("enroll_all") === "on",
    };
    try {
      const d = await api("/api/subjects", { method: "POST", body: JSON.stringify(body) });
      selectedSubject = d.subject.subject_id;
      selectedSection = "ALL";
      toast("Subject created.");
      await renderDashboard();
    } catch (err) {
      toast(err.message);
    }
  };
}

function renderTeacherSectionsBar() {
  if (user.role !== "Teacher") return "";
  const sectionBadges = currentSections.length
    ? currentSections.map((sec) => {
        const active = selectedSection === sec.section_name;
        return `<button type="button" class="${active ? "primary" : "secondary"}" data-section="${escapeHtml(sec.section_name)}">${escapeHtml(sec.section_name)} (${sec.student_count})</button>`;
      }).join("")
    : '<span class="muted">No sections enrolled</span>';

  return `
    <div class="section-bar">
      <div>
        <strong>Enrolled Sections in Subject:</strong>
        <div class="section-pills" style="margin-top:6px">
          <button type="button" class="${selectedSection === "ALL" ? "primary" : "secondary"}" data-section="ALL">All Sections (${currentRecords.length})</button>
          ${sectionBadges}
        </div>
      </div>
    </div>`;
}

function getFilteredRecords() {
  if (user.role !== "Teacher" || selectedSection === "ALL") return currentRecords;
  return currentRecords.filter((r) => r.course === selectedSection);
}

function renderComponentSummaryTable(record) {
  const c = record.breakdown.components;
  const rows = [
    { label: "Output", comp: c.output, weightPct: "25%" },
    { label: "Quizzes", comp: c.quiz, weightPct: "20%" },
    { label: "Recitation", comp: c.recitation, weightPct: "15%" },
    { label: "Exam", comp: c.exam, weightPct: "40%" },
  ];

  const bodyRows = rows.map((r) => {
    const scoreText = r.comp.is_complete
      ? `${r.comp.earned_points}/${r.comp.max_points}`
      : '<span class="incomplete-tag">Incomplete</span>';
    const pctText = r.comp.is_complete ? fmtPct(r.comp.percentage) : '<span class="incomplete-tag">Incomplete</span>';
    const itemsHtml = r.comp.assessments && r.comp.assessments.length
      ? `<ul class="muted" style="margin:6px 0 0 16px;padding:0">${
          r.comp.assessments.map((a) => {
            const pts = a.earned_points !== null && a.earned_points !== undefined
              ? `${a.earned_points}/${a.max_points}`
              : `Missing/${a.max_points}`;
            return `<li>${escapeHtml(a.name)}: <strong>${escapeHtml(pts)}</strong></li>`;
          }).join("")
        }</ul>`
      : "";

    return `
      <tr>
        <td><strong>${escapeHtml(r.label)}</strong>${itemsHtml}</td>
        <td>${scoreText}</td>
        <td>${pctText}</td>
        <td>${r.weightPct}</td>
      </tr>`;
  }).join("");

  return `
    <div class="breakdown-panel" id="breakdown-detail">
      <div class="toolbar" style="margin-bottom:10px">
        <h3 style="margin:0">Grade Breakdown — ${escapeHtml(record.student_name)} (${escapeHtml(record.student_id)}) · ${escapeHtml(record.course || "")}</h3>
        <button type="button" class="secondary" id="close-breakdown">Close</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Component</th><th>Score</th><th>Percentage</th><th>Weight</th></tr>
          </thead>
          <tbody>
            ${bodyRows}
            <tr>
              <td><strong>Final Grade</strong></td>
              <td></td>
              <td>${fmtFinalGrade(record.total_grade)}</td>
              <td><strong>100%</strong></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>`;
}

function openAssessmentModal(category, existing = null) {
  const isEdit = Boolean(existing);
  const activeCategory = existing ? existing.category : category;
  const titleLabel = isEdit ? `Edit ${activeCategory} Assessment` : `Add ${activeCategory} Assessment`;
  const defaultName = existing ? existing.name : "";
  const defaultMax = existing ? existing.max_points : (activeCategory === "Exam" ? 75 : 20);

  const scoreInputsHtml = currentRecords.map((rec) => {
    const compKey = activeCategory.toLowerCase();
    const list = rec.breakdown?.components?.[compKey]?.assessments || [];
    const found = existing ? list.find((a) => a.assessment_id === existing.id) : null;
    const val = found && found.earned_points !== null && found.earned_points !== undefined ? found.earned_points : "";
    return `
      <label class="field" style="margin:8px 0">
        ${escapeHtml(rec.student_name)} (${escapeHtml(rec.student_id)}) — ${escapeHtml(rec.course || "")}
        <input type="number" step="0.01" min="0" name="score_${escapeHtml(rec.student_id)}" value="${escapeHtml(val)}" placeholder="Optional earned points">
      </label>`;
  }).join("");

  const host = document.querySelector("#module");
  document.querySelector("#assessment-form")?.remove();
  host.insertAdjacentHTML("afterbegin", `
    <form id="assessment-form" class="panel" style="margin-bottom:16px">
      <h3>${escapeHtml(titleLabel)}</h3>
      <label class="field">Assessment Title
        <input type="text" name="name" value="${escapeHtml(defaultName)}" required placeholder="e.g., Output 1, Quiz 1, or Midterm Exam">
      </label>
      <label class="field">Maximum Points
        <input type="number" name="max_points" step="0.01" min="0.01" value="${escapeHtml(defaultMax)}" required>
      </label>
      <div class="field">
        <span>Student Earned Points (leave blank if not yet entered)</span>
        ${scoreInputsHtml}
      </div>
      <div class="actions">
        <button class="primary" type="submit">${isEdit ? "Save Assessment" : "Create Assessment"}</button>
        <button type="button" class="secondary" id="cancel-assessment">Cancel</button>
      </div>
    </form>`);

  document.querySelector("#cancel-assessment").onclick = () => document.querySelector("#assessment-form").remove();
  document.querySelector("#assessment-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const maxPts = Number(fd.get("max_points"));
    const scores = {};
    for (const rec of currentRecords) {
      const raw = String(fd.get(`score_${rec.student_id}`) ?? "").trim();
      if (raw !== "") {
        scores[rec.student_id] = Number(raw);
      } else if (isEdit) {
        scores[rec.student_id] = null;
      }
    }
    const body = {
      name: String(fd.get("name")).trim(),
      category: activeCategory,
      max_points: maxPts,
      scores,
    };
    try {
      if (isEdit) {
        await api(`/api/assessments/${encodeURIComponent(selectedSubject)}/${existing.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
        toast("Assessment updated and grades recalculated.");
      } else {
        await api(`/api/assessments/${encodeURIComponent(selectedSubject)}`, {
          method: "POST",
          body: JSON.stringify(body),
        });
        toast("Assessment added and grades recalculated.");
      }
      await renderModule("grades");
    } catch (err) {
      toast(err.message);
    }
  };
}

function renderTeacherAssessmentsPanel() {
  if (user.role !== "Teacher") return "";
  const cards = currentAssessments.length
    ? currentAssessments.map((a) => `
      <div class="assessment-card">
        <div class="assessment-card-head">
          <strong>${escapeHtml(a.name)}</strong>
          <span class="badge-cat">${escapeHtml(a.category)}</span>
        </div>
        <div class="muted">Max Points: <strong>${escapeHtml(a.max_points)}</strong></div>
        <div class="actions" style="margin-top:10px">
          <button type="button" class="secondary" data-edit-assess="${a.id}">Edit</button>
          <button type="button" class="danger" data-del-assess="${a.id}">Delete</button>
        </div>
      </div>`).join("")
    : '<p class="muted">No assessments created for this subject yet.</p>';

  return `
    <div class="breakdown-panel" style="margin-top:0;margin-bottom:16px">
      <div class="toolbar" style="margin-bottom:10px">
        <h3 style="margin:0">Assessments</h3>
        <div class="assessment-toolbar" style="margin-bottom:0">
          <button type="button" class="primary" id="add-output-btn">+ Add Output</button>
          <button type="button" class="primary" id="add-quiz-btn">+ Add Quiz</button>
          <button type="button" class="primary" id="add-exam-btn">+ Add Exam</button>
        </div>
      </div>
      <div class="assessment-list">${cards}</div>
    </div>`;
}

function bindSectionButtons(kind) {
  document.querySelectorAll("[data-section]").forEach((btn) => {
    btn.onclick = () => {
      selectedSection = btn.dataset.section;
      renderModule(kind);
    };
  });
}

async function renderModule(kind) {
  const host = document.querySelector("#module");
  host.innerHTML = "<p>Loading records...</p>";
  try {
    const d = await api(`/api/records/${encodeURIComponent(selectedSubject)}`);
    currentRecords = d.records || [];
    currentAssessments = d.assessments || [];
    currentSections = d.sections || [];
    const visibleRecords = getFilteredRecords();

    const notice = `<div class="notice">Grading: Output 25% · Quizzes 20% · Recitation 15% · Exam 40%. Attendance: every 3 lates = 1 absence; 8 or fewer total absences passes.</div>`;

    if (kind === "grades") {
      host.innerHTML = `
        ${notice}
        ${renderTeacherSectionsBar()}
        ${renderTeacherAssessmentsPanel()}
        <div id="editor-slot"></div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Student</th><th>ID</th>${user.role === "Teacher" ? "<th>Section</th>" : ""}
                <th>Output (25%)</th><th>Quiz (20%)</th><th>Recitation (15%)</th><th>Exam (40%)</th>
                <th>Final grade</th><th>Details</th>${user.role === "Teacher" ? "<th>Actions</th>" : ""}
              </tr>
            </thead>
            <tbody>
              ${visibleRecords.map((r) => `
                <tr>
                  <td>${escapeHtml(r.student_name)}</td>
                  <td>${escapeHtml(r.student_id)}</td>
                  ${user.role === "Teacher" ? `<td>${escapeHtml(r.course || "")}</td>` : ""}
                  <td>${fmtPct(r.output)}</td>
                  <td>${fmtPct(r.quiz)}</td>
                  <td>${fmtPct(r.recitation)}</td>
                  <td>${fmtPct(r.exam)}</td>
                  <td>${fmtFinalGrade(r.total_grade)}</td>
                  <td><button type="button" class="secondary" data-detail="${escapeHtml(r.student_id)}">View</button></td>
                  ${user.role === "Teacher" ? `<td><button type="button" class="secondary" data-grade="${escapeHtml(r.student_id)}">Edit Scores</button></td>` : ""}
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <div id="detail-slot"></div>`;

      bindSectionButtons("grades");

      host.querySelectorAll("[data-detail]").forEach((btn) => {
        btn.onclick = () => {
          const rec = currentRecords.find((r) => r.student_id === btn.dataset.detail);
          if (!rec) return;
          const slot = document.querySelector("#detail-slot");
          slot.innerHTML = renderComponentSummaryTable(rec);
          document.querySelector("#close-breakdown").onclick = () => { slot.innerHTML = ""; };
          slot.scrollIntoView({ behavior: "smooth", block: "nearest" });
        };
      });

      if (user.role === "Teacher") {
        document.querySelector("#add-output-btn").onclick = () => openAssessmentModal("Output");
        document.querySelector("#add-quiz-btn").onclick = () => openAssessmentModal("Quiz");
        document.querySelector("#add-exam-btn").onclick = () => openAssessmentModal("Exam");

        host.querySelectorAll("[data-edit-assess]").forEach((btn) => {
          btn.onclick = () => {
            const item = currentAssessments.find((a) => String(a.id) === btn.dataset.editAssess);
            if (item) openAssessmentModal(item.category, item);
          };
        });

        host.querySelectorAll("[data-del-assess]").forEach((btn) => {
          btn.onclick = async () => {
            const item = currentAssessments.find((a) => String(a.id) === btn.dataset.delAssess);
            if (!item) return;
            if (!confirm(`Delete "${item.name}" (${item.category})? Student grades will be recalculated.`)) return;
            try {
              await api(`/api/assessments/${encodeURIComponent(selectedSubject)}/${item.id}`, { method: "DELETE" });
              toast("Assessment deleted and grades recalculated.");
              await renderModule("grades");
            } catch (err) {
              toast(err.message);
            }
          };
        });

        host.querySelectorAll("[data-grade]").forEach((btn) => {
          btn.onclick = () => {
            const rec = currentRecords.find((r) => r.student_id === btn.dataset.grade);
            if (rec) editGrade(rec);
          };
        });
      }
    } else {
      host.innerHTML = `
        ${notice}
        ${renderTeacherSectionsBar()}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Student</th><th>ID</th>${user.role === "Teacher" ? "<th>Section</th>" : ""}
                <th>Present</th><th>Late</th><th>Absent</th><th>Total absences</th><th>Result</th>
                ${user.role === "Teacher" ? "<th>Actions</th>" : ""}
              </tr>
            </thead>
            <tbody>
              ${visibleRecords.map((r) => `
                <tr>
                  <td>${escapeHtml(r.student_name)}</td>
                  <td>${escapeHtml(r.student_id)}</td>
                  ${user.role === "Teacher" ? `<td>${escapeHtml(r.course || "")}</td>` : ""}
                  <td>${r.present}</td>
                  <td>${r.late}</td>
                  <td>${r.absent}</td>
                  <td>${r.attendance.total_absences}</td>
                  <td class="${r.attendance.is_passed ? "success" : "fail"}">${escapeHtml(r.attendance.status_label)}</td>
                  ${user.role === "Teacher" ? `
                    <td>
                      <div class="actions">
                        ${["present", "late", "absent"].map((f) => `<button class="secondary" data-att="${f}" data-id="${escapeHtml(r.student_id)}">+${f[0].toUpperCase()}</button>`).join("")}
                        <button class="danger" data-reset="${escapeHtml(r.student_id)}">Reset</button>
                      </div>
                    </td>` : ""}
                </tr>`).join("")}
            </tbody>
          </table>
        </div>`;

      bindSectionButtons("attendance");

      if (user.role === "Teacher") {
        host.querySelectorAll("[data-att]").forEach((btn) => {
          btn.onclick = async () => {
            try {
              await api(`/api/attendance/${encodeURIComponent(selectedSubject)}/${encodeURIComponent(btn.dataset.id)}`, {
                method: "PATCH",
                body: JSON.stringify({ field: btn.dataset.att, delta: 1 }),
              });
              renderModule("attendance");
            } catch (e) {
              toast(e.message);
            }
          };
        });
        host.querySelectorAll("[data-reset]").forEach((btn) => {
          btn.onclick = async () => {
            if (confirm("Reset all attendance counters for this student in this subject?")) {
              try {
                await api(`/api/attendance/${encodeURIComponent(selectedSubject)}/${encodeURIComponent(btn.dataset.reset)}/reset`, {
                  method: "POST",
                });
                renderModule("attendance");
              } catch (e) {
                toast(e.message);
              }
            }
          };
        });
      }
    }
  } catch (e) {
    host.innerHTML = `<p class="fail">${escapeHtml(e.message)}</p>`;
  }
}

async function editGrade(r) {
  const recScore = r.recitation ?? 0;
  const assessInputs = currentAssessments.map((a) => {
    const compList = r.breakdown?.components?.[a.category.toLowerCase()]?.assessments || [];
    const found = compList.find((x) => x.assessment_id === a.id);
    const val = found && found.earned_points !== null && found.earned_points !== undefined ? found.earned_points : "";
    return `
      <label class="field">
        ${escapeHtml(a.name)} (${escapeHtml(a.category)} — Max ${escapeHtml(a.max_points)})
        <input type="number" step="0.01" min="0" max="${escapeHtml(a.max_points)}" name="assess_${a.id}" value="${escapeHtml(val)}" placeholder="Enter score (0–${escapeHtml(a.max_points)})">
      </label>`;
  }).join("");

  const formHtml = `
    <form id="grade-form" class="panel" style="margin-bottom:16px">
      <h3>Edit scores — ${escapeHtml(r.student_name)} (${escapeHtml(r.student_id)})</h3>
      <label class="field">Recitation (0–100)
        <input type="number" name="recitation" min="0" max="100" step="0.01" value="${escapeHtml(recScore)}" required>
      </label>
      ${assessInputs}
      <div class="actions">
        <button class="primary" type="submit">Save grades</button>
        <button type="button" class="secondary" id="cancel-edit">Cancel</button>
      </div>
    </form>`;

  const editorSlot = document.querySelector("#editor-slot") || document.querySelector("#module");
  editorSlot.innerHTML = formHtml;
  editorSlot.scrollIntoView({ behavior: "smooth", block: "nearest" });

  document.querySelector("#cancel-edit").onclick = () => { editorSlot.innerHTML = ""; };
  document.querySelector("#grade-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const assessment_scores = currentAssessments.map((a) => {
      const raw = String(fd.get(`assess_${a.id}`) ?? "").trim();
      return {
        assessment_id: a.id,
        earned_points: raw === "" ? null : Number(raw),
      };
    });
    const body = {
      recitation: Number(fd.get("recitation")),
      assessment_scores,
    };
    try {
      await api(`/api/grades/${encodeURIComponent(selectedSubject)}/${encodeURIComponent(r.student_id)}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      toast("Student scores saved and recalculated.");
      await renderModule("grades");
    } catch (err) {
      toast(err.message);
    }
  };
}

async function renderResults() {
  const host = document.querySelector("#module");
  host.innerHTML = "<p>Loading results...</p>";
  try {
    const d = await api("/api/results");
    const avgDisplay = d.general_average !== null && d.general_average !== undefined
      ? d.general_average
      : '<span class="incomplete-tag">Incomplete</span>';

    host.innerHTML = `
      <div class="grid">
        <div class="stat">Student<strong>${escapeHtml(d.student.name)}</strong></div>
        <div class="stat">General average<strong>${avgDisplay}</strong></div>
        <div class="stat">Subjects<strong>${d.records.length}</strong></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Subject</th><th>Final grade</th><th>Total absences</th><th>Standing</th></tr>
          </thead>
          <tbody>
            ${d.records.map((r) => {
              let standingHtml;
              if (!r.attendance.is_passed) {
                standingHtml = '<td class="fail">FA / FAIL (Absences)</td>';
              } else if (r.total_grade === null || r.total_grade === undefined) {
                standingHtml = '<td class="incomplete-tag">Incomplete</td>';
              } else if (r.total_grade < 75) {
                standingHtml = '<td class="fail">FAILED (Academic)</td>';
              } else {
                standingHtml = '<td class="success">PASSED</td>';
              }
              return `
                <tr>
                  <td>${escapeHtml(r.subject_name)}</td>
                  <td>${fmtFinalGrade(r.total_grade)}</td>
                  <td>${r.attendance.total_absences}</td>
                  ${standingHtml}
                </tr>`;
            }).join("")}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    host.textContent = e.message;
  }
}

document.querySelector("#start-login").onclick = showLogin;
(async () => {
  try { await api("/api/csrf"); } catch (_) {}
  try {
    const d = await api("/api/auth/me");
    user = d.user;
    await renderDashboard();
  } catch (_) {}
})();