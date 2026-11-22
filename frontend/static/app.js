const app = document.querySelector("#app");
const workspace = document.querySelector("#workspace");
const navUser = document.querySelector("#nav-user");
let user = null, selectedSubject = "DB101", students = [], csrfToken = "";

async function api(path, options={}) {
  const method = (options.method || "GET").toUpperCase();
  const response = await fetch(path, {
    credentials:"same-origin",
    ...options,
    headers:{
      "Content-Type":"application/json",
      ...(method !== "GET" ? {"X-CSRF-Token": csrfToken} : {}),
      ...(options.headers||{})
    }
  });
  const data = await response.json().catch(()=>({}));
  if (data.csrf_token) csrfToken = data.csrf_token;
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function toast(message){const el=document.querySelector("#toast");el.textContent=message;el.style.display="block";setTimeout(()=>el.style.display="none",2600)}
function escapeHtml(value){return String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function showLogin(){
  document.querySelector(".hero").hidden=true;workspace.hidden=false;
  workspace.innerHTML=`<form class="login" id="login-form"><h2>Log in to GRATTEN</h2>
    <p class="muted">Use your student ID or registered email.</p>
    <label class="field">Email / ID<input name="identity" required autocomplete="username"></label>
    <label class="field">Password<input name="password" type="password" required autocomplete="current-password"></label>
    <button class="primary" type="submit">Log in</button>
    <p class="muted">Demo student: 2026-8812 / Student123!<br>Demo teacher: teacher@olopsc.edu.ph / Teacher123!</p></form>`;
  document.querySelector("#login-form").addEventListener("submit",async e=>{
    e.preventDefault();const f=new FormData(e.currentTarget);
    try{const d=await api("/api/auth/login",{method:"POST",body:JSON.stringify({identity:f.get("identity"),password:f.get("password")})});user=d.user;await renderDashboard()}
    catch(err){toast(err.message)}
  });
}
async function renderDashboard(){
  document.querySelector(".hero").hidden=true;workspace.hidden=false;
  navUser.innerHTML=`<span>${escapeHtml(user.name||user.teacher_name||user.login_id)} · ${escapeHtml(user.role)}</span> <button id="logout">Logout</button>`;
  document.querySelector("#logout").onclick=async()=>{await api("/api/auth/logout",{method:"POST"});user=null;navUser.innerHTML="";workspace.hidden=true;document.querySelector(".hero").hidden=false};
  const [s, st] = await Promise.all([api("/api/subjects"),api("/api/students")]);
  students=st.students;
  if(!s.subjects.some(x=>x.subject_id===selectedSubject)) selectedSubject=s.subjects[0]?.subject_id;
  workspace.innerHTML=`<section class="panel">
    <div class="toolbar"><div><p class="eyebrow">Academic Year 2026–2027 · First Semester</p><h2>Academic dashboard</h2></div>
      <div class="tabs"><button class="secondary" id="grades-tab">Grades</button><button class="secondary" id="attendance-tab">Attendance</button><button class="secondary" id="results-tab">Results</button></div></div>
    <div class="field"><label for="subject">Subject</label><select id="subject">${s.subjects.map(x=>`<option value="${escapeHtml(x.subject_id)}">${escapeHtml(x.code)} — ${escapeHtml(x.name)}</option>`).join("")}</select></div>
    <div id="module"></div></section>`;
  const select=document.querySelector("#subject");select.value=selectedSubject;
  select.onchange=()=>{selectedSubject=select.value;renderModule("grades")};
  document.querySelector("#grades-tab").onclick=()=>renderModule("grades");
  document.querySelector("#attendance-tab").onclick=()=>renderModule("attendance");
  document.querySelector("#results-tab").onclick=()=>renderResults();
  renderModule("grades");
}
async function renderModule(kind){
  const host=document.querySelector("#module");host.innerHTML="<p>Loading records…</p>";
  try{
    const d=await api(`/api/records/${encodeURIComponent(selectedSubject)}`);
    host.innerHTML=`<div class="notice">Grading: Output 25% · Quizzes 20% · Recitation 15% · Exam 40%. Attendance: every 3 lates = 1 absence; 8 or fewer total absences passes.</div>
      <div class="table-wrap"><table><thead><tr><th>Student</th><th>ID</th>${kind==="grades"?"<th>Output</th><th>Quiz</th><th>Recitation</th><th>Exam</th><th>Final grade</th>":"<th>Present</th><th>Late</th><th>Absent</th><th>Total absences</th><th>Result</th>"}${user.role==="Teacher"?"<th>Actions</th>":""}</tr></thead><tbody>
      ${d.records.map(r=>`<tr><td>${escapeHtml(r.student_name)}</td><td>${escapeHtml(r.student_id)}</td>
      ${kind==="grades"?`<td>${r.output}</td><td>${r.quiz}</td><td>${r.recitation}</td><td>${r.exam}</td><td><strong>${r.total_grade}</strong></td>`:
      `<td>${r.present}</td><td>${r.late}</td><td>${r.absent}</td><td>${r.attendance.total_absences}</td><td class="${r.attendance.is_passed?"success":"fail"}">${r.attendance.status_label}</td>`}
      ${user.role==="Teacher"?`<td>${kind==="grades"?`<button class="secondary" data-grade="${escapeHtml(r.student_id)}">Edit</button>`:
      `<div class="actions">${["present","late","absent"].map((f)=>`<button class="secondary" data-att="${f}" data-id="${escapeHtml(r.student_id)}">+${f[0].toUpperCase()}</button>`).join("")}<button class="danger" data-reset="${escapeHtml(r.student_id)}">Reset</button></div>`}</td>`:""}
      </tr>`).join("")}</tbody></table></div>`;
    if(user.role==="Teacher"&&kind==="grades"){
      host.querySelectorAll("[data-grade]").forEach(btn=>btn.onclick=()=>editGrade(d.records.find(r=>r.student_id===btn.dataset.grade)));
    }
    if(user.role==="Teacher"&&kind==="attendance"){
      host.querySelectorAll("[data-att]").forEach(btn=>btn.onclick=async()=>{try{await api(`/api/attendance/${selectedSubject}/${btn.dataset.id}`,{method:"PATCH",body:JSON.stringify({field:btn.dataset.att,delta:1})});renderModule("attendance")}catch(e){toast(e.message)}});
      host.querySelectorAll("[data-reset]").forEach(btn=>btn.onclick=async()=>{if(confirm("Reset all attendance counters for this student in this subject?")){try{await api(`/api/attendance/${selectedSubject}/${btn.dataset.reset}/reset`,{method:"POST"});renderModule("attendance")}catch(e){toast(e.message)}}});
    }
  }catch(e){host.innerHTML=`<p class="fail">${escapeHtml(e.message)}</p>`}
}
async function editGrade(r){
  const values={output:r.output,quiz:r.quiz,recitation:r.recitation,exam:r.exam};
  const formHtml=`<form id="grade-form" class="panel"><h3>Edit grades — ${escapeHtml(r.student_name)}</h3>${Object.entries(values).map(([k,v])=>`<label class="field">${k[0].toUpperCase()+k.slice(1)}<input type="number" name="${k}" min="0" max="100" step="0.01" value="${v}" required></label>`).join("")}<button class="primary">Save grades</button> <button type="button" class="secondary" id="cancel-edit">Cancel</button></form>`;
  const host=document.querySelector("#module");host.insertAdjacentHTML("afterbegin",formHtml);
  document.querySelector("#cancel-edit").onclick=()=>document.querySelector("#grade-form").remove();
  document.querySelector("#grade-form").onsubmit=async e=>{e.preventDefault();const fd=new FormData(e.currentTarget);const body=Object.fromEntries(["output","quiz","recitation","exam"].map(k=>[k,Number(fd.get(k))]));try{await api(`/api/grades/${selectedSubject}/${r.student_id}`,{method:"PUT",body:JSON.stringify(body)});toast("Grades saved.");renderModule("grades")}catch(err){toast(err.message)}};
}
async function renderResults(){
  const host=document.querySelector("#module");host.innerHTML="<p>Loading results…</p>";
  try{const d=await api("/api/results");host.innerHTML=`<div class="grid"><div class="stat">Student<strong>${escapeHtml(d.student.name)}</strong></div><div class="stat">General average<strong>${d.general_average}</strong></div><div class="stat">Subjects<strong>${d.records.length}</strong></div></div><div class="table-wrap"><table><thead><tr><th>Subject</th><th>Final grade</th><th>Total absences</th><th>Standing</th></tr></thead><tbody>${d.records.map(r=>`<tr><td>${escapeHtml(r.subject_name)}</td><td>${r.total_grade}</td><td>${r.attendance.total_absences}</td><td class="${r.attendance.is_passed&&r.total_grade>=75?"success":"fail"}">${!r.attendance.is_passed?"FA / FAIL (Absences)":r.total_grade<75?"FAILED (Academic)":"PASSED"}</td></tr>`).join("")}</tbody></table></div>`}
  catch(e){host.textContent=e.message}
}
document.querySelector("#start-login").onclick=showLogin;
(async()=>{
  try{ await api("/api/csrf") }catch(_){}
  try{const d=await api("/api/auth/me");user=d.user;await renderDashboard()}catch(_){}
})();