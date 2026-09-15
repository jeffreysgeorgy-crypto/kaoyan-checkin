// ============================================================
// 考研学习打卡 —— 应用逻辑
// ============================================================

/* ---------- 工具函数 ---------- */
function parseDate(str) {
  const [y, m, d] = str.split('-').map(Number);
  return new Date(y, m - 1, d);
}
function pad(n) { return String(n).padStart(2, '0'); }
function toStr(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
function todayStr() { return toStr(new Date()); }
function addDays(dateStr, n) {
  const d = parseDate(dateStr);
  d.setDate(d.getDate() + n);
  return toStr(d);
}
// 1=周一 ... 7=周日
function getWeekday(dateStr) {
  return (parseDate(dateStr).getDay() + 6) % 7 + 1;
}
function getWeekNumber(dateStr) {
  const diff = Math.round((parseDate(dateStr) - parseDate(SEMESTER_START)) / 86400000);
  return Math.floor(diff / 7) + 1;
}
function daysUntil(target) {
  return Math.round((parseDate(target) - parseDate(todayStr())) / 86400000);
}
// 用户可在"我的-设置"里覆盖考研 / 六级日期，未设置时用 data.js 常量
function getExamDate(state) { return (state && state.examDate) || EXAM_DATE; }
function getCet6Date(state) {
  return (state && state.cet6Date) || (typeof CET6_EXAM_DATE !== 'undefined' ? CET6_EXAM_DATE : null);
}
function isHoliday(dateStr) {
  return dateStr >= NATIONAL_HOLIDAY.from && dateStr <= NATIONAL_HOLIDAY.to;
}
function parseWeeks(str) {
  const set = new Set();
  String(str).split(',').forEach(function (p) {
    p = p.trim();
    if (!p) return;
    if (p.indexOf('-') > 0) {
      const parts = p.split('-').map(Number);
      for (let i = parts[0]; i <= parts[1]; i++) set.add(i);
    } else {
      set.add(Number(p));
    }
  });
  return set;
}
function formatDateCN(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return y + '年' + m + '月' + d + '日';
}
function range(a, b) {
  const arr = [];
  for (let i = a; i <= b; i++) arr.push(i);
  return arr;
}
function fmtMinutes(min) {
  min = Math.max(0, Math.round(min));
  const h = Math.floor(min / 60), m = min % 60;
  if (h > 0) return h + ' 小时' + (m ? ' ' + m + ' 分' : '');
  return m + ' 分钟';
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

const WEEKDAY_NAMES = ['', '周一', '周二', '周三', '周四', '周五', '周六', '周日'];

/* ---------- 状态存取 ---------- */
const STORE_KEY = 'kaoyan_study_checkin_v1';

function loadState() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY)) || {};
  } catch (e) { return {}; }
}
function saveState(state) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  } catch (e) { console.warn('保存失败', e); }
}
// 旧版把“背单词”作为复选框任务；新版改为独立单词块，这里清掉旧任务
function migrateState(state) {
  state.days = state.days || {};
  Object.keys(state.days).forEach(function (k) {
    const d = state.days[k];
    if (d && d.tasks) d.tasks = d.tasks.filter(function (t) { return t.id !== 'words'; });
  });
  // v2 迁移：清除旧版时间轴（课程块/空白块模式），新版从 DAILY_PLAN_DATA 重新生成
  if (!state.migrated_v2) {
    Object.keys(state.days).forEach(function (k) {
      if (state.days[k] && state.days[k].timeline) delete state.days[k].timeline;
    });
    state.migrated_v2 = true;
  }
  // v3 迁移：课表变动（周二清空、政治课改上课标记、新增六级任务），重建全部时间轴与待办池
  if (!state.migrated_v3) {
    Object.keys(state.days).forEach(function (k) {
      const d = state.days[k];
      if (!d) return;
      delete d.timeline;
      delete d.pendingPool;
      d.rolloverDone = false;
    });
    state.migrated_v3 = true;
  }
  // v4 迁移：课表再次更新（周二第三大节政治课、作息统一 14:30/19:00-21:00、政治恢复替换），重建时间轴
  if (!state.migrated_v4) {
    Object.keys(state.days).forEach(function (k) {
      const d = state.days[k];
      if (!d) return;
      delete d.timeline;
      delete d.pendingPool;
      d.rolloverDone = false;
    });
    state.migrated_v4 = true;
  }
  // v5 迁移：旧版可能在同一天时间轴/待办池里留下重复块（同 id 两份）。按 id 去重，保留打卡进度，不清数据
  if (!state.migrated_v5) {
    Object.keys(state.days).forEach(function (k) {
      const d = state.days[k];
      if (!d) return;
      if (Array.isArray(d.timeline) && d.timeline.length) d.timeline = dedupeBlocks(d.timeline);
      if (Array.isArray(d.pendingPool) && d.pendingPool.length) {
        const seen = {};
        d.pendingPool = d.pendingPool.filter(function (b) {
          const key = (b.origId || b.id) + '@' + (b.from || '');
          if (seen[key]) return false;
          seen[key] = true;
          return true;
        });
      }
    });
    state.migrated_v5 = true;
  }
  return state;
}

/* 时间轴块去重：同 id 只保留打卡进度最完整的一份（已完成 > 评分 > 有效时长 > 手工编辑） */
function blockProgressScore(b) {
  return (b.customEdit ? 1 : 0) * 200000 +
    (b.done ? 100000 : 0) +
    (b.rating || 0) * 1000 +
    (b.actualMinutes || 0);
}
function dedupeBlocks(blocks) {
  const map = new Map();
  let noId = 0;
  blocks.forEach(function (b) {
    const key = b.id ? ('id:' + b.id) : ('__noid__' + (noId++));
    const prev = map.get(key);
    if (!prev || blockProgressScore(b) > blockProgressScore(prev)) map.set(key, b);
  });
  return Array.from(map.values());
}

/* ---------- 今日课表 ---------- */
function getTodayCourses(dateStr) {
  const wd = getWeekday(dateStr);
  if (wd > 5 || isHoliday(dateStr)) return [];
  const week = getWeekNumber(dateStr);
  const list = SCHEDULE[wd] || [];
  return list
    .filter(function (c) { return parseWeeks(c.weeks).has(week); })
    .sort(function (a, b) { return a.period - b.period; });
}

/* ---------- 中文数字转阿拉伯数字 ---------- */
function cn2num(str) {
  const m = { '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10 };
  if (str.length === 1) return m[str] || 0;
  if (str.length === 2 && str[0] === '十') return 10 + m[str[1]];
  if (str.length === 2 && str[1] === '十') return m[str[0]] * 10;
  if (str.length === 3) return m[str[0]] * 10 + m[str[2]];
  return 0;
}

/* ---------- 从 DAILY_PLAN_DATA 查找某天计划 ---------- */
function getPlanForDate(dateStr) {
  if (typeof DAILY_PLAN_DATA === 'undefined') return null;
  return DAILY_PLAN_DATA.find(function (d) { return d.date === dateStr; }) || null;
}

/* ---------- 数学一轮总进度（高数12章 + 线代20讲 + 概率31讲 = 63 讲） ---------- */
function getMathProgress(dateStr) {
  var MATH_HIGH = 12, MATH_LINEAR = 20, MATH_PROB = 31;
  var TOTAL = MATH_HIGH + MATH_LINEAR + MATH_PROB; // 63
  if (typeof DAILY_PLAN_DATA === 'undefined') return { pct: 0, label: '', hint: '' };

  // 圈码 ①-⑳㉑-㉛ → 1-31
  var CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛';
  function parseLesson(str, subject) {
    // 高数用"第X章"格式
    var m1 = str.match(/第([一二三四五六七八九十]+)章/);
    if (m1) return cn2num(m1[1]);
    // 线代/概率用"科目①"格式
    var m2 = str.match(new RegExp(subject + '([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛])'));
    if (m2) return CIRCLED.indexOf(m2[1]) + 1;
    return 0;
  }

  // 1. 从今天往前找最近一条可解析的记录
  var matched = null;
  for (var i = DAILY_PLAN_DATA.length - 1; i >= 0; i--) {
    if (DAILY_PLAN_DATA[i].date > dateStr) continue;
    var subj = DAILY_PLAN_DATA[i].mathSubject || '';
    var ch = parseLesson(DAILY_PLAN_DATA[i].mathContent || '', subj);
    if (ch > 0) { matched = { data: DAILY_PLAN_DATA[i], chapter: ch }; break; }
  }

  // 2. 往前没找到 → 往后找最近一条（计划还没开始的情况）
  var upcoming = null;
  if (!matched) {
    for (var j = 0; j < DAILY_PLAN_DATA.length; j++) {
      var subj2 = DAILY_PLAN_DATA[j].mathSubject || '';
      var ch2 = parseLesson(DAILY_PLAN_DATA[j].mathContent || '', subj2);
      if (ch2 > 0) { upcoming = { data: DAILY_PLAN_DATA[j], chapter: ch2 }; break; }
    }
  }
  if (!matched && !upcoming) return { pct: 0, label: '无章节计划', hint: '' };

  // 即将开始（今天在计划开始前），显示"即将开始"并给 0%
  if (!matched && upcoming) {
    var ud = upcoming.data;
    var uLabel = '';
    if (ud.mathSubject === '高数')      uLabel = '即将开始：高数 第 ' + upcoming.chapter + '/' + MATH_HIGH + ' 章';
    else if (ud.mathSubject === '线代')  uLabel = '即将开始：线代 第 ' + upcoming.chapter + '/' + MATH_LINEAR + ' 讲';
    else if (ud.mathSubject === '概率')  uLabel = '即将开始：概率 第 ' + upcoming.chapter + '/' + MATH_PROB + ' 讲';
    else uLabel = '即将开始：' + ud.mathSubject;
    return { pct: 0, label: uLabel, hint: '计划于 ' + ud.date + ' 开始' };
  }

  var d = matched.data;
  var ch = matched.chapter;
  var absUnit = 0, label = '';
  if (d.mathSubject === '高数')      { absUnit = ch;                       label = '高数：第 ' + ch + '/' + MATH_HIGH + ' 章'; }
  else if (d.mathSubject === '线代')  { absUnit = MATH_HIGH + ch;           label = '线代：第 ' + ch + '/' + MATH_LINEAR + ' 讲'; }
  else if (d.mathSubject === '概率')  { absUnit = MATH_HIGH + MATH_LINEAR + ch; label = '概率：第 ' + ch + '/' + MATH_PROB + ' 讲'; }
  else { return { pct: 0, label: d.mathSubject, hint: '' }; }

  // 今天的计划（用于显示"今日数学任务"小字）
  var today = getPlanForDate(dateStr);
  var hint = today && today.mathTime && today.mathTime !== '—'
    ? '今日数学任务：' + today.mathContent + '（' + today.mathTime + '）'
    : '';
  return { pct: Math.round(absUnit / TOTAL * 100), label: label, hint: hint };
}

/* ---------- 渲染数学进度条 ---------- */
function renderMathProgress() {
  const el = document.getElementById('mathProgress');
  if (!el) return;
  const p = getMathProgress(todayStr());
  el.innerHTML =
    '<div class="mp-head"><span class="mp-label">数学一轮 · ' + escapeHtml(p.label) + '</span><span class="mp-pct">' + p.pct + '%</span></div>' +
    '<div class="mp-bar"><div class="mp-fill" style="width:' + p.pct + '%"></div></div>' +
    (p.hint ? '<div class="mp-hint">' + escapeHtml(p.hint) + '</div>' : '');
}

/* ---------- 任务生成（从 DAILY_PLAN_DATA 读取） ---------- */
function generateTasks(dateStr) {
  const plan = getPlanForDate(dateStr);
  const tasks = [];
  if (!plan) {
    // 回退：无计划数据时用旧逻辑
    const wd = getWeekday(dateStr);
    tasks.push({ id: 'math', type: 'exam', text: '数学：武忠祥网课 + 严选题' });
    const subject = MAJOR_SUBJECT[wd] || '计算机组成原理';
    tasks.push({ id: 'major', type: 'exam', text: '专业课：' + subject });
    return tasks;
  }
  // 数学
  if (plan.mathTime && plan.mathTime !== '—') {
    const t = plan.mathTime.split('；')[0];
    tasks.push({ id: 'plan-math', type: 'exam', text: '[' + t + '] 数学：' + plan.mathContent });
  }
  // 数据结构
  if (plan.dsTime && plan.dsTime !== '—') {
    tasks.push({ id: 'plan-ds', type: 'exam', text: '[' + plan.dsTime + '] 数据结构：' + plan.dsContent });
  }
  // 计组
  if (plan.csTime && plan.csTime !== '—') {
    tasks.push({ id: 'plan-cs', type: 'exam', text: '[' + plan.csTime + '] 计组：' + plan.csContent });
  }
  // 英语
  if (plan.englishTime && plan.englishTime !== '—') {
    tasks.push({ id: 'plan-eng', type: 'exam', text: '[' + plan.englishTime + '] 英语：' + plan.englishContent });
  }
  return tasks;
}

/* ---------- 单词：累计 / 复习 / 每日需背 ---------- */
function getTotalLearned(state) {
  let total = 0;
  const days = state.days || {};
  Object.keys(days).forEach(function (k) {
    const w = days[k].words;
    if (w) total += (w.newCount || 0);
  });
  return total;
}
function getReviewDue(state, dateStr) {
  let total = 0;
  REVIEW_INTERVALS.forEach(function (i) {
    const d = addDays(dateStr, -i);
    const day = state.days && state.days[d];
    if (day && day.words) total += (day.words.newCount || 0);
  });
  return total;
}
function getDailyRequired(state) {
  const remain = getWordTarget(state) - getTotalLearned(state);
  if (remain <= 0) return 0;
  const days = Math.max(1, daysUntil(getExamDate(state)));
  return Math.ceil(remain / days);
}

/* ---------- 保证某天记录存在并合并任务 ---------- */
function ensureDay(state, dateStr) {
  state.days = state.days || {};
  let day = state.days[dateStr];
  if (!day) {
    day = {
      tasks: [],
      words: { newCount: 0, reviewCount: 0 },
      problems: { math: 0, cs: 0 },
      reflection: { accomplishment: '', confusion: '', tomorrow: '' },
      pomodoros: 0,
      studyMinutes: 0,
    };
    state.days[dateStr] = day;
  }
  const doneMap = {};
  (day.tasks || []).forEach(function (t) { doneMap[t.id] = t.done; });

  const base = generateTasks(dateStr);
  const due = getReviewDue(state, dateStr);
  if (due > 0) base.push({ id: 'review', type: 'review', text: '复习旧词：' + due + ' 个' });

  day.tasks = base.map(function (t) {
    return { id: t.id, type: t.type, text: t.text, done: !!doneMap[t.id] };
  });
  day.words = day.words || { newCount: 0, reviewCount: 0 };
  day.problems = day.problems || { math: 0, cs: 0 };
  day.reflection = day.reflection || { accomplishment: '', confusion: '', tomorrow: '' };
  day.pomodoros = day.pomodoros || 0;
  day.studyMinutes = day.studyMinutes || 0;
  return day;
}

/* ---------- 某天的“可打卡项”（用于完成率） ---------- */
function getDayItems(state, dateStr) {
  const day = state.days && state.days[dateStr];
  if (!day) return [];
  const items = day.tasks.map(function (t) { return { done: !!t.done }; });
  const w = day.words || {};
  items.push({ done: (w.newCount || 0) + (w.reviewCount || 0) > 0 });
  const p = day.problems || {};
  items.push({ done: (p.math || 0) + (p.cs || 0) > 0 });
  return items;
}

/* ---------- 渲染：头部 ---------- */
function renderHeader() {
  const today = todayStr();
  const wd = getWeekday(today);
  const week = getWeekNumber(today);
  const weekText = week >= 1 ? '第 ' + week + ' 周' : '未开学';
  document.getElementById('todayInfo').textContent =
    formatDateCN(today) + ' ' + WEEKDAY_NAMES[wd] + ' · ' + weekText;

  const remain = daysUntil(getExamDate(loadState()));
  const cd = document.getElementById('countdown');
  if (remain > 0) {
    cd.innerHTML = '距离 2027 年考研初试（28 考研）还有 <span class="days">' + remain + '</span> 天';
  } else if (remain === 0) {
    cd.innerHTML = '今天就是考研初试，加油！';
  } else {
    cd.innerHTML = '考研初试已过去 <span class="days">' + (-remain) + '</span> 天';
  }
  renderMathProgress();
}

/* ---------- 渲染：考研进度 ---------- */
function defaultProgress() { return { math: { current: 1 }, major: { current: 1, total: 12 } }; }

function fillSelect(sel, options, textFn) {
  sel.innerHTML = '';
  options.forEach(function (v) {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = textFn(v);
    sel.appendChild(o);
  });
}

function renderProgress(state) {
  if (!state.progress) state.progress = defaultProgress();
  const p = state.progress;

  const mathSel = document.getElementById('mathChapter');
  fillSelect(mathSel, range(1, MATH_TOTAL), function (v) { return '第 ' + v + ' 章'; });
  mathSel.value = p.math.current;
  const mathPct = Math.round(p.math.current / MATH_TOTAL * 100);
  document.getElementById('mathPct').textContent = '第 ' + p.math.current + ' / 10 章 · ' + mathPct + '%';
  document.getElementById('mathBar').style.width = mathPct + '%';

  const totalSel = document.getElementById('majorTotal');
  fillSelect(totalSel, MAJOR_TOTAL_OPTIONS, function (v) { return '共 ' + v + ' 章'; });
  totalSel.value = p.major.total;
  const chapterSel = document.getElementById('majorChapter');
  fillSelect(chapterSel, range(1, p.major.total), function (v) { return '第 ' + v + ' 章'; });
  if (p.major.current > p.major.total) p.major.current = p.major.total;
  chapterSel.value = p.major.current;
  const majorPct = Math.round(p.major.current / p.major.total * 100);
  document.getElementById('majorPct').textContent = '第 ' + p.major.current + ' / ' + p.major.total + ' 章 · ' + majorPct + '%';
  document.getElementById('majorBar').style.width = majorPct + '%';
}

/* ---------- 渲染：单词进度 ---------- */
function renderWordProgress(state) {
  const target = getWordTarget(state);
  const total = getTotalLearned(state);
  const remain = Math.max(0, target - total);
  const required = getDailyRequired(state);
  const pct = Math.min(100, Math.round(total / target * 100));
  document.getElementById('wordProgress').innerHTML =
    '<div class="progress-head"><span>考研词汇</span><span class="progress-pct">' + total + ' / ' + target + ' · ' + pct + '%</span></div>' +
    '<div class="bar"><div class="bar-fill fill-word" style="width:' + pct + '%"></div></div>' +
    '<div class="word-hint">剩余 ' + remain + ' 词 · 每日需背 ' + required + ' 个才能按时完成</div>';
}

/* ---------- 渲染：今日课表 ---------- */
function renderCourses(dateStr) {
  const el = document.getElementById('todayCourses');
  const wd = getWeekday(dateStr);
  const week = getWeekNumber(dateStr);
  let html = '';
  if (wd > 5) {
    html = '<p class="empty">今天周末，无课程安排 🎉</p>';
  } else if (isHoliday(dateStr)) {
    html = '<p class="empty">国庆假期，无课程安排 🎉</p>';
  } else {
    const courses = getTodayCourses(dateStr);
    if (courses.length === 0) {
      html = '<p class="empty">今天（第 ' + week + ' 周）没有课程安排</p>';
    } else {
      html = courses.map(function (c) {
        const p = PERIODS.find(function (x) { return x.period === c.period; });
        const time = p ? (p.start + ' - ' + p.end) : '';
        return '<div class="course">' +
          '<div class="course-top"><span class="badge">' + p.name + '</span><span class="course-time">' + time + '</span></div>' +
          '<div class="course-name">' + c.name + '</div>' +
          '<div class="course-meta">' + c.teacher + (c.location ? ' · ' + c.location : '') + '</div>' +
        '</div>';
      }).join('');
    }
  }
  el.innerHTML = html;
}

/* ---------- 渲染：单词块 / 刷题块（输入框只在对应分类有任务时存在） ---------- */
function updateWordHint(state, dateStr) {
  const hint = document.getElementById('wordHint');
  if (!hint) return;
  const ws = getWordSettings(state);
  const day = state.days && state.days[dateStr];
  const newDone = (day && day.words && day.words.newCount) || 0;
  const reviewDone = (day && day.words && day.words.reviewCount) || 0;
  const newLeft = Math.max(0, ws.dailyNew - newDone);
  const reviewLeft = Math.max(0, ws.dailyReview - reviewDone);
  const total = getTotalLearned(state);
  const parts = [];
  parts.push('今日新词 <b>' + newDone + ' / ' + ws.dailyNew + '</b>' + (newLeft ? '（还差 ' + newLeft + '）' : ' ✅'));
  parts.push('今日复习 <b>' + reviewDone + ' / ' + ws.dailyReview + '</b>' + (reviewLeft ? '（还差 ' + reviewLeft + '）' : ' ✅'));
  parts.push('总累计 <b>' + total + '</b> / ' + ws.target);
  hint.innerHTML = parts.join(' · ');
}

function renderWordBlock(state, dateStr) {
  const day = ensureDay(state, dateStr);
  const n = document.getElementById('wordNew');
  const r = document.getElementById('wordReview');
  if (n) n.value = day.words.newCount || '';
  if (r) r.value = day.words.reviewCount || '';
  updateWordHint(state, dateStr);
}

function renderProblemBlock(state, dateStr) {
  const day = ensureDay(state, dateStr);
  const m = document.getElementById('probMath');
  const c = document.getElementById('probCs');
  if (m) m.value = day.problems.math || '';
  if (c) c.value = day.problems.cs || '';
}

/* ---------- 渲染：任务 ---------- */
const TASK_TAGS = { exam: '考研', course: '课内', review: '复习' };

function renderTasks(state, dateStr) {
  const day = ensureDay(state, dateStr);
  document.getElementById('taskList').innerHTML = day.tasks.map(function (t) {
    return '<label class="task ' + (t.done ? 'done' : '') + '">' +
      '<input type="checkbox" data-id="' + t.id + '"' + (t.done ? ' checked' : '') + '>' +
      '<span class="task-text">' + escapeHtml(t.text) + '</span>' +
      '<span class="task-tag ' + t.type + '">' + (TASK_TAGS[t.type] || '') + '</span>' +
    '</label>';
  }).join('');
  updateTaskSummary(state, dateStr);
}

function updateTaskSummary(state, dateStr) {
  const items = getDayItems(state, dateStr);
  const done = items.filter(function (i) { return i.done; }).length;
  const total = items.length;
  const el = document.getElementById('taskSummary');
  if (total === 0) { el.textContent = ''; return; }
  el.textContent = (done === total) ? '🎉 今日任务全部完成！' : '已完成 ' + done + ' / ' + total;
}

/* ---------- 渲染：反思 ---------- */
function renderReflection(state, dateStr) {
  const day = ensureDay(state, dateStr);
  document.getElementById('reflectAccomplishment').value = day.reflection.accomplishment || '';
  document.getElementById('reflectConfusion').value = day.reflection.confusion || '';
  document.getElementById('reflectTomorrow').value = day.reflection.tomorrow || '';
  renderReflectionHistory(state, dateStr);
}

/* ---------- 反思页：历史反思列表 ---------- */
function renderReflectionHistory(state, dateStr) {
  const el = document.getElementById('reflectionHistory');
  if (!el) return;
  const days = state.days || {};
  // 收集最近 14 天内有反思内容的记录
  const records = [];
  for (let i = 1; i <= 14; i++) {
    const d = addDays(dateStr, -i);
    const day = days[d];
    if (!day || !day.reflection) continue;
    const r = day.reflection;
    if (!(r.accomplishment || r.confusion || r.tomorrow)) continue;
    records.push({ date: d, reflection: r });
  }
  if (records.length === 0) {
    el.innerHTML = '<p class="empty">暂无历史反思记录，坚持每天记录一点点 📝</p>';
    return;
  }
  el.innerHTML = records.map(function (rec) {
    const r = rec.reflection;
    return '<div class="rh-item">' +
      '<div class="rh-date">' + formatDateCN(rec.date) + '</div>' +
      (r.accomplishment ? '<div class="rh-row"><span class="rh-tag accent">成就感</span><span class="rh-text">' + escapeHtml(r.accomplishment) + '</span></div>' : '') +
      (r.confusion ? '<div class="rh-row"><span class="rh-tag warn">困惑</span><span class="rh-text">' + escapeHtml(r.confusion) + '</span></div>' : '') +
      (r.tomorrow ? '<div class="rh-row"><span class="rh-tag info">明日优先</span><span class="rh-text">' + escapeHtml(r.tomorrow) + '</span></div>' : '') +
    '</div>';
  }).join('');
}

/* ---------- 今日：单词进度小卡片 ---------- */
function renderWordMini(state, dateStr) {
  const el = document.getElementById('wordMini');
  if (!el) return;
  const ws = getWordSettings(state);
  const total = getTotalLearned(state);
  const day = state.days && state.days[dateStr];
  const newDone = (day && day.words && day.words.newCount) || 0;
  const reviewDone = (day && day.words && day.words.reviewCount) || 0;
  const newLeft = Math.max(0, ws.dailyNew - newDone);
  const reviewLeft = Math.max(0, ws.dailyReview - reviewDone);
  const pct = Math.min(100, Math.round(total / ws.target * 100));
  el.innerHTML =
    '<div class="mini-item"><span class="mini-label">累计已背</span><span class="mini-num">' + total + '</span><span class="mini-sub">/ ' + ws.target + '（' + pct + '%）</span></div>' +
    '<div class="mini-item"><span class="mini-label">今日新词还差</span><span class="mini-num accent">' + newLeft + '</span><span class="mini-sub">/' + ws.dailyNew + ' 个</span></div>' +
    '<div class="mini-item"><span class="mini-label">今日复习还差</span><span class="mini-num accent">' + reviewLeft + '</span><span class="mini-sub">/' + ws.dailyReview + ' 个</span></div>';
}

/* ---------- 今日：分类任务清单（与时间轴共用同一份 day.timeline 数据） ---------- */
const CATEGORY_DEFS = [
  { key: 'math', icon: '📐', title: '数学',     subjects: ['math'] },
  { key: 'cs',   icon: '💻', title: '数据结构 / 计组（408）', subjects: ['ds', 'cs'] },
  { key: 'eng',  icon: '🔤', title: '英语',     subjects: ['eng'] },
  { key: 'cet6', icon: '🎧', title: '六级冲刺', subjects: ['cet6'] },
];

/* ---------- 工作台 2：学科筛选器（任务清单页用） ---------- */
let currentSubjectFilter = 'all';
const SUBJECT_TABS = [
  { key: 'all',   label: '全部' },
  { key: 'math',  label: '数学' },
  { key: 'ds',    label: '数据结构' },
  { key: 'cs',    label: '计算机组成原理' },
  { key: 'eng',   label: '英语' },
  { key: 'other', label: '其他' },
];
function matchSubjectFilter(block, filter) {
  if (filter === 'all') return true;
  if (filter === 'eng') return block.subject === 'eng' || block.subject === 'cet6';
  if (filter === 'other') return ['math', 'ds', 'cs', 'eng', 'cet6'].indexOf(block.subject) < 0;
  return block.subject === filter;
}

function collectTaskBlocks(state, dateStr) {
  const day = ensureDay(state, dateStr);
  const list = [];
  const seen = {}; // 防御：同 id 的脏数据块只显示一次
  function add(b, isPending) {
    if (b.id) {
      if (seen[b.id]) return;
      seen[b.id] = true;
    }
    list.push({ block: b, isPending: isPending });
  }
  (day.pendingPool || []).forEach(function (b) { add(b, true); });
  (day.timeline || []).forEach(function (b) { add(b, false); });
  return list.filter(function (r) { return r.block.type !== 'info'; });
}

function renderCategoryCard(b, isPending) {
  const focusing = pomo.running && pomo.mode === 'focus' && pomo.taskId === b.id;
  const timeText = isPending ? '📌 待办池' : (b.start ? (b.start + ' - ' + b.end) : '自由时间');
  return '<div class="cat-task' + (b.done ? ' done' : '') + (focusing ? ' focusing' : '') + (isPending ? ' pending' : '') + '">' +
    '<div class="cat-task-head">' +
      '<input type="checkbox" class="cat-check" data-tldone="' + b.id + '"' + (b.done ? ' checked' : '') + ' aria-label="打卡">' +
      '<div class="cat-task-main">' +
        '<div class="cat-task-name">' + escapeHtml(b.name) + '</div>' +
        '<div class="cat-task-meta">' + timeText +
          (isPending && b.from ? ' · 来自 ' + formatDateCN(b.from) : '') +
          (b.note ? ' · ' + escapeHtml(b.note) : '') +
        '</div>' +
      '</div>' +
    '</div>' +
    '<div class="cat-task-foot">' +
      renderStars(b.id, b.rating || 0) +
      (b.subtasks && b.subtasks.length
        ? '<span class="tl-min-label">有效时长 <b>' + (b.actualMinutes || 0) + '</b> 分（子项合计）</span>'
        : '<label class="tl-min-label">有效时长 <input type="number" class="tl-minutes" data-tlmin="' + b.id + '" min="0" max="600" value="' + (b.actualMinutes || '') + '" placeholder="0" inputmode="numeric"> 分</label>') +
      '<button class="tl-focus-btn" data-tlfocus="' + b.id + '"' + (focusing ? ' disabled' : '') + '>' + (focusing ? '⏹ 专注中' : '▶ 开始专注') + '</button>' +
    '</div>' +
    renderSubtaskGroup(b) +
  '</div>';
}

/* 子任务组渲染：独立打卡 + 独立时长（引用格式 blockId::subKey） */
function renderSubtaskGroup(b) {
  if (!b.subtasks || !b.subtasks.length) return '';
  let html = '<div class="subtask-group">';
  b.subtasks.forEach(function (s) {
    const ref = b.id + '::' + s.key;
    html += '<div class="subtask-item' + (s.done ? ' done' : '') + '">' +
      '<input type="checkbox" data-tldone="' + ref + '"' + (s.done ? ' checked' : '') + ' aria-label="子任务打卡">' +
      '<span class="subtask-name">' + escapeHtml(s.name) + '</span>' +
      '<label class="subtask-min"><input type="number" data-tlmin="' + ref + '" min="0" max="600" value="' + (s.actualMinutes || '') + '" placeholder="0" inputmode="numeric"> 分</label>' +
    '</div>';
  });
  const sum = b.subtasks.reduce(function (a, s) { return a + (s.actualMinutes || 0); }, 0);
  html += '<div class="subtask-sum">合计有效时长：<b>' + sum + '</b> 分钟</div></div>';
  return html;
}

function renderTodayChecklist(state, dateStr) {
  const el = document.getElementById('categoryTasks');
  if (!el) return;
  const rows = collectTaskBlocks(state, dateStr);
  const allTasks = rows.map(function (r) { return r.block; });
  const doneCount = allTasks.filter(function (b) { return b.done; }).length;
  const poolCount = rows.filter(function (r) { return r.isPending; }).length;

  let html = '<div class="cat-progress-bar">今日完成 <b>' + doneCount + ' / ' + allTasks.length + '</b>' +
    '<div class="bar"><div class="bar-fill fill-math" style="width:' + (allTasks.length ? Math.round(doneCount / allTasks.length * 100) : 0) + '%"></div></div>' +
    (poolCount ? '<span class="pool-tag">📌 待办池 ' + poolCount + ' 项</span>' : '') +
  '</div>';

  CATEGORY_DEFS.forEach(function (cat) {
    const items = rows.filter(function (r) { return cat.subjects.indexOf(r.block.subject) >= 0; });
    html += '<section class="cat-group"><h3 class="cat-title">' + cat.icon + ' ' + cat.title + '</h3>';
    if (items.length === 0) {
      html += '<p class="empty cat-empty">今日无安排</p>';
    } else {
      // 英语分类顶部放单词/刷题输入（ID 与原逻辑保持一致）
      if (cat.key === 'eng') {
        html += '<div class="cat-inputs">' +
          '<label>今日新学 <input type="number" id="wordNew" min="0" inputmode="numeric" placeholder="0"> 个</label>' +
          '<label>今日复习 <input type="number" id="wordReview" min="0" inputmode="numeric" placeholder="0"> 个</label>' +
        '</div><div class="word-hint" id="wordHint"></div>';
      }
      if (cat.key === 'math') {
        html += '<div class="cat-inputs"><label>今日刷题 <input type="number" id="probMath" min="0" inputmode="numeric" placeholder="0"> 道</label></div>';
      }
      if (cat.key === 'cs') {
        html += '<div class="cat-inputs"><label>今日 408 刷题 <input type="number" id="probCs" min="0" inputmode="numeric" placeholder="0"> 道</label></div>';
      }
      items.forEach(function (r) { html += renderCategoryCard(r.block, r.isPending); });
    }
    html += '</section>';
  });

  // 政治分类：今天有政治课时提示认真听讲（时间轴槽位已自动替换为考研任务）
  const politics = (!isHoliday(dateStr) ? getTodayCourses(dateStr) : []).filter(function (c) {
    return c.name && c.name.indexOf(POLITICS_KEYWORD) >= 0;
  });
  html += '<section class="cat-group"><h3 class="cat-title">📖 政治</h3>';
  if (politics.length === 0) {
    html += '<p class="empty cat-empty">今日无安排</p>';
  } else {
    politics.forEach(function (c) {
      const p = PERIODS.find(function (x) { return x.period === c.period; });
      html += '<div class="cat-task politics-note"><div class="cat-task-head"><div class="cat-task-main">' +
        '<div class="cat-task-name">🏫 认真听讲政治课</div>' +
        '<div class="cat-task-meta">' + (p ? p.name + ' ' + p.start + '-' + p.end : '') + ' · ' + escapeHtml(c.name) +
        ' · 该时段已在时间轴自动替换为考研任务</div>' +
      '</div></div></div>';
    });
  }
  html += '</section>';

  el.innerHTML = html;
}

/* ---------- 工作台 2：分类任务清单页（学科切换栏 + 任务卡片清单） ---------- */
function renderTasksPage(state, dateStr) {
  const tabsEl = document.getElementById('subjectTabs');
  const listEl = document.getElementById('tasksList');
  if (!tabsEl || !listEl) return;
  // 学科切换栏
  tabsEl.innerHTML = SUBJECT_TABS.map(function (t) {
    return '<button class="subject-tab' + (currentSubjectFilter === t.key ? ' active' : '') + '" data-subj="' + t.key + '">' + t.label + '</button>';
  }).join('');
  // 收集任务并按当前学科过滤
  const rows = collectTaskBlocks(state, dateStr);
  const filtered = rows.filter(function (r) { return matchSubjectFilter(r.block, currentSubjectFilter); });
  const allTasks = filtered.map(function (r) { return r.block; });
  const doneCount = allTasks.filter(function (b) { return b.done; }).length;
  const poolCount = filtered.filter(function (r) { return r.isPending; }).length;

  let html = '<div class="cat-progress-bar">完成 <b>' + doneCount + ' / ' + allTasks.length + '</b>' +
    '<div class="bar"><div class="bar-fill fill-math" style="width:' + (allTasks.length ? Math.round(doneCount / allTasks.length * 100) : 0) + '%"></div></div>' +
    (poolCount ? '<span class="pool-tag">📌 待办池 ' + poolCount + ' 项</span>' : '') +
  '</div>';

  // 今日统计输入区（总是渲染，保证 renderWordBlock/renderProblemBlock 能找到输入框）
  html += '<div class="cat-inputs-row">' +
    '<label>今日新学 <input type="number" id="wordNew" min="0" inputmode="numeric" placeholder="0"> 个</label>' +
    '<label>今日复习 <input type="number" id="wordReview" min="0" inputmode="numeric" placeholder="0"> 个</label>' +
    '<label>今日刷题 <input type="number" id="probMath" min="0" inputmode="numeric" placeholder="0"> 道</label>' +
    '<label>今日 408 刷题 <input type="number" id="probCs" min="0" inputmode="numeric" placeholder="0"> 道</label>' +
  '</div><div class="word-hint" id="wordHint"></div>';

  // 任务卡片列表（扁平展示，不按学科分组）
  if (filtered.length === 0) {
    html += '<p class="empty cat-empty">该学科今日无任务</p>';
  } else {
    filtered.forEach(function (r) { html += renderCategoryCard(r.block, r.isPending); });
  }

  // 政治课提示（"全部"或"其他"模式下显示）
  if (currentSubjectFilter === 'all' || currentSubjectFilter === 'other') {
    const politics = (!isHoliday(dateStr) ? getTodayCourses(dateStr) : []).filter(function (c) {
      return c.name && c.name.indexOf(POLITICS_KEYWORD) >= 0;
    });
    if (politics.length) {
      html += '<section class="cat-group"><h3 class="cat-title">📖 政治</h3>';
      politics.forEach(function (c) {
        const p = PERIODS.find(function (x) { return x.period === c.period; });
        html += '<div class="cat-task politics-note"><div class="cat-task-head"><div class="cat-task-main">' +
          '<div class="cat-task-name">🏫 认真听讲政治课</div>' +
          '<div class="cat-task-meta">' + (p ? p.name + ' ' + p.start + '-' + p.end : '') + ' · ' + escapeHtml(c.name) +
          ' · 该时段已在时间轴自动替换为考研任务</div>' +
        '</div></div></div>';
      });
      html += '</section>';
    }
  }

  listEl.innerHTML = html;
  // 输入框值同步（renderWordBlock/renderProblemBlock 已有空值保护）
  renderWordBlock(state, dateStr);
  renderProblemBlock(state, dateStr);
}

/* ---------- 课表页：我的固定课表（周一~周五按钮切换，每次只显示一天） ---------- */
let selectedFixedWeekday = (function () {
  const wd = getWeekday(todayStr());
  return (wd >= 1 && wd <= 5) ? wd : 1; // 周末默认显示周一
})();

function renderFixedSchedule() {
  const el = document.getElementById('fixedSchedule');
  if (!el) return;
  // 5 个星期按钮
  let html = '<div class="fs-tabs">';
  for (let wd = 1; wd <= 5; wd++) {
    html += '<button type="button" class="fs-tab' + (wd === selectedFixedWeekday ? ' active' : '') + '" data-fswd="' + wd + '">' + WEEKDAY_NAMES[wd] + '</button>';
  }
  html += '</div>';

  // 只渲染选中那一天的固定课程
  const wd = selectedFixedWeekday;
  const list = (SCHEDULE[wd] || []).slice().sort(function (a, b) { return a.period - b.period; });
  html += '<div class="fs-list"><div class="fs-list-head">' + WEEKDAY_NAMES[wd] + '固定课程 · 共 ' + list.length + ' 门</div>';
  if (!list.length) {
    html += '<p class="empty">' + WEEKDAY_NAMES[wd] + '无课 🎉</p>';
  } else {
    list.forEach(function (c) {
      const p = PERIODS.find(function (x) { return x.period === c.period; });
      const isPolitics = c.name && c.name.indexOf(POLITICS_KEYWORD) >= 0;
      html += '<div class="fs-item' + (isPolitics ? ' politics' : '') + '">' +
        '<div class="fs-time">' + (p ? p.name + ' ' + p.start + '-' + p.end : '') + '</div>' +
        '<div class="fs-info"><div class="fs-name">' + escapeHtml(c.name) + '</div>' +
          '<div class="fs-meta">' + escapeHtml(c.teacher || '') + ' · 第' + c.weeks + '周' + (c.location ? ' · ' + escapeHtml(c.location) : '') + '</div>' +
        '</div>' +
      '</div>';
    });
  }
  html += '</div>';
  el.innerHTML = html;
}

/* ---------- 设置页 ---------- */
function renderSettings(state) {
  const examInput = document.getElementById('setExamDate');
  const cet6Input = document.getElementById('setCet6Date');
  if (examInput) examInput.value = getExamDate(state);
  if (cet6Input) cet6Input.value = getCet6Date(state);
  renderPomoPref();
}

/* ---------- 课表冲突校验：检查时间轴任务是否与真实上课节次重叠 ---------- */
function timeToMin(t) {
  if (!t) return null;
  const m = t.match(/^(\d{1,2}):(\d{2})$/);
  return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : null;
}
function intervalsOverlap(aS, aE, bS, bE) {
  const a1 = timeToMin(aS), a2 = timeToMin(aE), b1 = timeToMin(bS), b2 = timeToMin(bE);
  if (a1 === null || a2 === null || b1 === null || b2 === null) return false;
  return a1 < b2 && b1 < a2;
}
function validateTimelineConflicts(dateStr) {
  const conflicts = [];
  const plan = getPlanForDate(dateStr);
  if (!plan || isHoliday(dateStr)) return conflicts;
  const courses = getTodayCourses(dateStr);
  if (!courses.length) return conflicts;
  // 只校验从计划表生成的 plan-* 任务；政治替换块/六级块本身就是按空档插入，不参与校验
  const blocks = generateTimeline(dateStr).filter(function (b) {
    return /^plan-/.test(b.id) && b.start && b.end;
  });
  courses.forEach(function (c) {
    const p = PERIODS.find(function (x) { return x.period === c.period; });
    if (!p) return;
    blocks.forEach(function (b) {
      if (intervalsOverlap(b.start, b.end, p.start, p.end)) {
        conflicts.push({
          date: dateStr, block: b.name, blockTime: b.start + '-' + b.end,
          course: c.name, courseTime: p.name + ' ' + p.start + '-' + p.end,
        });
      }
    });
  });
  return conflicts;
}
/* 校验计划范围内全部日期，返回冲突列表；可用 console.table(validateAllTimelines()) 查看 */
function validateAllTimelines() {
  const out = [];
  (typeof DAILY_PLAN_DATA !== 'undefined' ? DAILY_PLAN_DATA : []).forEach(function (d) {
    validateTimelineConflicts(d.date).forEach(function (c) { out.push(c); });
  });
  return out;
}

/* ---------- 渲染：历史 ---------- */
let historyFullOnly = false;

function renderHistory(state) {
  const days = state.days || {};
  const today = todayStr();
  let keys = Object.keys(days).filter(function (k) { return k < today; }).sort().reverse();

  const fullKeys = keys.filter(function (k) {
    const items = getDayItems(state, k);
    return items.length > 0 && items.every(function (i) { return i.done; });
  });

  document.getElementById('historyStat').textContent =
    '累计完美打卡 ' + fullKeys.length + ' 天 / 共记录 ' + keys.length + ' 天';

  if (historyFullOnly) keys = keys.filter(function (k) { return fullKeys.indexOf(k) !== -1; });

  const listEl = document.getElementById('historyList');
  if (keys.length === 0) {
    listEl.innerHTML = '<p class="empty">' + (historyFullOnly ? '还没有完美打卡的记录' : '还没有历史记录') + '</p>';
    return;
  }

  listEl.innerHTML = keys.map(function (k) {
    const d = days[k];
    const items = getDayItems(state, k);
    const done = items.filter(function (i) { return i.done; }).length;
    const full = items.length > 0 && done === items.length;
    const pomos = d.pomodoros || 0;
    const words = (d.words && d.words.newCount) || 0;
    const probs = ((d.problems && d.problems.math) || 0) + ((d.problems && d.problems.cs) || 0);
    return '<div class="history-item ' + (full ? 'full' : '') + '">' +
      '<div class="history-date">' + formatDateCN(k) + ' ' + WEEKDAY_NAMES[getWeekday(k)] + '</div>' +
      '<div class="history-info">' + done + '/' + items.length + (pomos ? ' · 🍅' + pomos : '') + (words ? ' · 词' + words : '') + (probs ? ' · 题' + probs : '') + '</div>' +
      (full ? '<span class="history-badge">全部完成 ✓</span>' : '') +
    '</div>';
  }).join('');
}

/* ---------- 时间轴（日程表） ---------- */
let editingBlock = { id: null };

function nowTime() {
  const d = new Date();
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

/* ---------- 科目标签 ---------- */
const SUBJECT_LABELS = { math: '数学', ds: '数据结构', cs: '计组', eng: '英语', cet6: '六级', other: '竞赛/AI' };

/* ---------- 时间段解析：从 "08:30–11:30；20:30–21:00错题" 提取第一个时间段 ---------- */
function parseTimeRange(timeStr) {
  if (!timeStr || timeStr === '—') return null;
  const first = timeStr.split(/[；,;]/)[0];
  const m = first.match(/(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})/);
  return m ? { start: m[1], end: m[2] } : null;
}

/* ---------- 在 timeline 和 pendingPool 中查找块 ---------- */
function findTimelineBlock(state, dateStr, blockId) {
  const day = state.days && state.days[dateStr];
  if (!day) return null;
  if (day.pendingPool) {
    const p = day.pendingPool.find(function (b) { return b.id === blockId; });
    if (p) return p;
  }
  if (day.timeline) {
    return day.timeline.find(function (b) { return b.id === blockId; });
  }
  return null;
}

/* 任务引用：支持 blockId 与 blockId::subKey（子任务颗粒度拆解） */
function splitTaskRef(ref) {
  const s = String(ref || '');
  const i = s.indexOf('::');
  if (i < 0) return { blockId: s, subKey: null };
  return { blockId: s.slice(0, i), subKey: s.slice(i + 2) };
}
function findTaskRef(state, dateStr, ref) {
  const parts = splitTaskRef(ref);
  const block = findTimelineBlock(state, dateStr, parts.blockId);
  let sub = null;
  if (block && parts.subKey && block.subtasks) {
    sub = block.subtasks.filter(function (x) { return x.key === parts.subKey; })[0] || null;
  }
  return { block: block, sub: sub, subKey: parts.subKey };
}

/* ---------- 跨天滚动：把昨天未完成的任务移到今天的待办池 ---------- */
function rolloverPendingTasks(state, today) {
  const day = state.days[today];
  if (!day) return false;
  if (day.rolloverDone) return false; // 今天已滚动过

  const yesterday = addDays(today, -1);
  const yDay = state.days[yesterday];
  if (!yDay) { day.rolloverDone = true; return true; }

  // 从昨天的 timeline 和 pendingPool 中收集未完成任务
  // 功能 C：标 noRollover 的任务（如建模周）不补债，不进入待办池
  const undone = [];
  if (yDay.timeline) {
    yDay.timeline.forEach(function (b) {
      if (b.type === 'task' && !b.done && !b.noRollover) undone.push(b);
    });
  }
  if (yDay.pendingPool) {
    yDay.pendingPool.forEach(function (b) {
      if (!b.done) undone.push(b);
    });
  }

  if (undone.length > 0) {
    day.pendingPool = day.pendingPool || [];
    const week = getWeekNumber(today);
    state.procrastination = state.procrastination || { week: week, counts: {} };
    // 跨周重置拖延计数
    if (state.procrastination.week !== week) {
      state.procrastination = { week: week, counts: {} };
    }
    undone.forEach(function (b) {
      const rolled = {
        id: 'pending-' + (b.origId || b.id) + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
        origId: b.origId || b.id,
        subject: b.subject || '',
        name: b.name,
        from: yesterday,
        done: false, rating: 0, actualMinutes: 0,
      };
      if (b.subtasks) {
        rolled.subtasks = b.subtasks.map(function (s) {
          return { key: s.key, name: s.name, done: false, rating: 0, actualMinutes: 0 };
        });
      }
      day.pendingPool.push(rolled);
      if (b.subject) {
        state.procrastination.counts[b.subject] = (state.procrastination.counts[b.subject] || 0) + 1;
      }
    });
  }

  day.rolloverDone = true;
  return true;
}

/* ---------- 任务颗粒度拆解：各科目子任务模板（独立打卡 / 评分 / 时长） ---------- */
const SUBTASK_TEMPLATES = {
  math: [
    { key: 'video', name: '① 看网课（武忠祥）' },
    { key: 'basic', name: '② 基础30讲习题' },
    { key: 'yan',   name: '③ 严选题' },
  ],
  ds: [
    { key: 'video', name: '① 看王道课程' },
    { key: 'ex',    name: '② 王道课后题' },
  ],
  cs: [
    { key: 'video', name: '① 看王道课程' },
    { key: 'ex',    name: '② 王道课后题' },
  ],
  eng: [
    { key: 'new',      name: '① 新词 50-55 个' },
    { key: 'review',   name: '② 旧词复习' },
    { key: 'sentence', name: '③ 长难句 2 句' },
  ],
};
function makeSubtasks(subject) {
  const tpl = SUBTASK_TEMPLATES[subject];
  if (!tpl) return null;
  return tpl.map(function (t) {
    return { key: t.key, name: t.name, done: false, rating: 0, actualMinutes: 0 };
  });
}

/* ---------- 从 DAILY_PLAN_DATA 生成今日时间轴（严格按计划表逐块生成） ---------- */
function generateTimeline(dateStr) {
  const plan = getPlanForDate(dateStr);
  const blocks = [];
  if (!plan) return blocks;

  function addBlock(id, subject, name, timeStr, opts) {
    // 表格里 "—"、空 或 "暂停" 的科目不生成打卡任务
    if (!timeStr || timeStr === '—' || /暂停/.test(timeStr)) return;
    const range = parseTimeRange(timeStr);
    blocks.push(Object.assign({
      id: id, type: 'task', subject: subject,
      name: name, note: '',
      start: range ? range.start : '',
      end: range ? range.end : '',
      done: false, rating: 0, actualMinutes: 0,
    }, opts || {}));
  }

  // 数学：整块显示表格具体内容，下拆 3 个独立打卡子任务（看视频/讲义、基础题、严选题）
  addBlock('plan-math', 'math', '数学：' + plan.mathContent, plan.mathTime, { subtasks: makeSubtasks('math') });
  // 数据结构 / 计组
  addBlock('plan-ds', 'ds', '数据结构：' + plan.dsContent, plan.dsTime, { subtasks: makeSubtasks('ds') });
  addBlock('plan-cs', 'cs', '计组：' + plan.csContent, plan.csTime, { subtasks: makeSubtasks('cs') });
  // 英语：拆成 3 个独立打卡块（新词 / 复习 / 长难句）
  addEnglishBlocks(plan, addBlock);
  // 竞赛/AI
  addBlock('plan-other', 'other', '竞赛/AI：' + plan.otherContent, plan.otherTime);

  // 自动避让：与当天真实课程节次撞车的计划块，整体平移到当天最近的空闲时段（不改内容/时长）
  avoidClassConflicts(blocks, dateStr);

  // 六级冲刺任务（考前 30 天内，嵌入晚间碎片时间；独立于计划表五科目）
  addCet6Tasks(blocks, dateStr, false);

  // 按开始时间排序（无时间的排到最后）
  blocks.sort(function (a, b) {
    return (a.start || '99:99').localeCompare(b.start || '99:99');
  });
  return blocks;
}

/* 英语拆分：严格按计划表 englishContent 文本拆成独立打卡块。
   类别：新词 / 复习 / 长难句（后期出现阅读精读时归入长难句块，标签显示“长难句/阅读”）。
   文本里没有的类别不生成（不凭空造任务）。 */
function addEnglishBlocks(plan, addBlock) {
  const t = plan.englishTime;
  if (!t || t === '—' || /暂停/.test(t)) return;
  const c = (plan.englishContent || '').replace(/^单词[：:]/, '');
  const buckets = { 'new': [], 'review': [], 'sentence': [] };

  // 先按中文分号/句号切段，再按 “ + ”（带空格）拆小项；括号内的“分析+翻译”不会被拆开
  c.split(/[；;。]/).forEach(function (seg) {
    seg.split(/\s+\+\s+/).forEach(function (raw) {
      const token = raw.trim();
      if (!token) return;
      // “50个新词/复习滚动”：同一项里新词与复习用 “/” 连写，拆成两个小项
      if (/新词/.test(token) && /复习|滚动/.test(token) && /\//.test(token) && !/长难句|阅读/.test(token)) {
        token.split('/').forEach(function (piece) { classifyEnglishToken(piece, buckets); });
      } else {
        classifyEnglishToken(token, buckets);
      }
    });
  });

  const hasReading = buckets.sentence.some(function (s) { return /阅读/.test(s); });
  const defs = [
    { key: 'new',      id: 'plan-eng-new',      label: '英语·新词' },
    { key: 'review',   id: 'plan-eng-review',   label: '英语·复习' },
    { key: 'sentence', id: 'plan-eng-sentence', label: hasReading ? '英语·长难句/阅读' : '英语·长难句' },
  ];
  defs.forEach(function (d) {
    if (!buckets[d.key].length) return;
    addBlock(d.id, 'eng', d.label + '：' + buckets[d.key].join('；'), t);
  });
}

function classifyEnglishToken(token, buckets) {
  const x = token.replace(/^单词[：:]/, '').trim();
  if (!x) return;
  if (/新词/.test(x)) { buckets['new'].push(x); return; }
  if (/长难句|阅读|精读|选项/.test(x)) { buckets.sentence.push(x); return; }
  if (/复习|滚动|错词|熟词|单词|保持不断档/.test(x)) { buckets.review.push(x); return; }
  buckets.review.push(x); // 无法归类的碎片并入复习
}

/* 自动避让：把与当天真实课程节次重叠的计划块整体平移到"距原时间最近"的空闲时段。
   - 只改 start/end，不改任务内容与时长；DAILY_PLAN_DATA 原始时间保持不动
   - 同一天内 07:30–22:30、步长 5 分钟搜索；同时避开其他计划块
   - 同起点的块（如数学竞赛块嵌套在数学块里）视为一组整体移动
   - 当天确实放不下时保留原位并加 note 标注，由 validateAllTimelines 继续报出 */
function minToTime(m) {
  const h = Math.floor(m / 60), mm = m % 60;
  return (h < 10 ? '0' : '') + h + ':' + (mm < 10 ? '0' : '') + mm;
}
function avoidClassConflicts(blocks, dateStr) {
  if (isHoliday(dateStr)) return;
  const courses = getTodayCourses(dateStr);
  if (!courses.length) return;
  const busy = courses.map(function (c) {
    return PERIODS.find(function (p) { return p.period === c.period; });
  }).filter(Boolean).map(function (p) {
    return { start: timeToMin(p.start), end: timeToMin(p.end) };
  });
  const DAY_START = timeToMin('07:30');
  const DAY_END = timeToMin('23:00'); // 极少数五节全满日（第15-16周周四）允许最晚到 22:45 结束
  const STEP = 5;

  const timed = blocks.filter(function (b) { return /^plan-/.test(b.id) && b.start && b.end; });
  const groups = [];
  timed.forEach(function (b) {
    const same = groups.filter(function (g) { return g.start === b.start; })[0];
    if (same) same.blocks.push(b);
    else groups.push({ start: b.start, blocks: [b] });
  });
  // 已占用区间：课程节次 + 已落位的计划组
  const placed = busy.slice();
  groups.sort(function (a, b) { return a.start < b.start ? -1 : 1; });
  groups.forEach(function (g) {
    const spans = g.blocks.map(function (b) {
      return { s: timeToMin(b.start), e: timeToMin(b.end) };
    });
    const gStart = Math.min.apply(null, spans.map(function (x) { return x.s; }));
    const gEnd = Math.max.apply(null, spans.map(function (x) { return x.e; }));
    const dur = gEnd - gStart;
    const hitClass = busy.some(function (z) { return gStart < z.end && z.start < gEnd; });
    if (!hitClass) { placed.push({ start: gStart, end: gEnd }); return; }

    let best = null;
    for (let t = DAY_START; t + dur <= DAY_END; t += STEP) {
      const fit = !placed.some(function (z) { return t < z.end && z.start < t + dur; });
      if (fit) {
        const delta = Math.abs(t - gStart);
        if (best === null || delta < best.delta) best = { t: t, delta: delta };
      }
    }
    if (best) {
      const shift = best.t - gStart;
      g.blocks.forEach(function (b) {
        b.start = minToTime(timeToMin(b.start) + shift);
        b.end = minToTime(timeToMin(b.end) + shift);
      });
      placed.push({ start: best.t, end: best.t + dur });
    } else {
      g.blocks.forEach(function (b) {
        b.note = (b.note ? b.note + ' · ' : '') + '⚠️ 当天无完整空档，请手动调整时间';
      });
      placed.push({ start: gStart, end: gEnd });
    }
  });
}

/* ---------- 六级倒计时与冲刺任务 ---------- */
function cet6DaysLeft(dateStr) {
  const d = getCet6Date(loadState());
  if (!d) return null;
  return Math.round((parseDate(d) - parseDate(dateStr)) / 86400000);
}

/* 时间轴顶部红色倒计时小卡片 */
function renderCet6Alert(dateStr) {
  const el = document.getElementById('cet6Alert');
  if (!el) return;
  const d = cet6DaysLeft(dateStr);
  if (d === null || d < 0 || d > CET6_PREP_DAYS) { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  if (d === 0) {
    el.innerHTML = '🎯 今天是 <b>六级考试日</b>！带齐准考证、身份证、耳机，加油！';
  } else {
    el.innerHTML = '⏰ 距离六级考试还有 <b>' + d + '</b> 天 · 今日必刷 <b>1 套听力 / 阅读</b>';
  }
}

/* 按倒计时阶段把六级任务塞进晚间碎片时间（不占数学/408 大块） */
function addCet6Tasks(blocks, dateStr, isHeavyDay) {
  const d = cet6DaysLeft(dateStr);
  if (d === null || d < 0 || d > CET6_PREP_DAYS) return;

  // 考试当天：只放一条鼓励信息块
  if (d === 0) {
    blocks.push({
      id: 'cet6-examday-' + dateStr, type: 'info', subject: 'cet6',
      name: '🎯 六级考试日！加油！', note: '今日不安排考研任务，全力应考',
      start: '', end: '', done: false, rating: 0, actualMinutes: 0,
    });
    return;
  }

  // 重任务日（建模/周三全天课）：自适应缩减为只背 10 个六级单词
  if (isHeavyDay) {
    blocks.push({
      id: 'cet6-lite-' + dateStr, type: 'task', subject: 'cet6',
      name: '六级高频词 10 个（考研重任务日，自适应缩减）', note: '六级冲刺 · 缩减版',
      start: '21:00', end: '21:15',
      done: false, rating: 0, actualMinutes: 0, noRollover: true,
    });
    return;
  }

  // 若当天晚上有政治课（第五大节 19:00-21:00），六级任务顺延到下课后
  const hasEveningClass = getTodayCourses(dateStr).some(function (c) {
    return c.name && c.name.indexOf(POLITICS_KEYWORD) >= 0 && c.period === 5;
  });
  const eveStart = hasEveningClass ? '21:05' : '19:30';
  function cet(id, name, start, end) {
    blocks.push({
      id: 'cet6-' + id + '-' + dateStr, type: 'task', subject: 'cet6',
      name: name, note: '六级冲刺' + (hasEveningClass ? ' · 晚课后顺延' : ''),
      start: start, end: end,
      done: false, rating: 0, actualMinutes: 0, noRollover: true,
    });
  }

  if (d <= 9) {
    // 考前 10-1 天：作文/翻译模板 + 核心词汇；无晚课时加睡前 15 分钟磨耳朵
    cet('write', '六级作文/翻译模板背诵 + 核心词汇复习', eveStart, hasEveningClass ? '21:50' : '20:15');
    if (!hasEveningClass) cet('ear', '睡前听力"磨耳朵" 15 分钟', '21:45', '22:00');
  } else if (d <= 19) {
    // 考前 20-10 天：仔细阅读 2 篇 + 泛听；周六下午整套模考
    cet('read', '六级阅读 2 篇（仔细阅读）+ 听力泛听', eveStart, hasEveningClass ? '21:50' : '20:15');
    if (getWeekday(dateStr) === 6) {
      cet('mock', '六级真题模考（完整一套，掐时间做）', '14:00', '15:30');
    }
  } else {
    // 考前 30-20 天：精听 1 篇 + 30 个高频词
    cet('listen', '六级听力精听 1 篇真题 + 背 30 个高频词', eveStart, hasEveningClass ? '21:35' : '20:00');
  }
}

function ensureTimeline(state, dateStr) {
  const day = ensureDay(state, dateStr);
  // 跨天滚动：只对真实"今天"生效；查看历史/未来日期时不得产生待办池等副作用
  if (dateStr === todayStr()) {
    const rolled = rolloverPendingTasks(state, dateStr);
    if (rolled) saveState(state);
  }
  syncTimelineWithTemplate(day, dateStr);
  return day.timeline;
}

/* 与最新计划模板同步：
   - 同 id 块保留打卡进度（done/评分/有效时长），但时间/名称/备注更新为模板值（拿到最新避让/替换结果）
   - 用户手工编辑过的块（customEdit）整块保留
   - 模板里已不存在的旧块自动消失；模板新增的块自动出现
   - 幂等：每次渲染都可安全调用 */
function syncTimelineWithTemplate(day, dateStr) {
  const template = generateTimeline(dateStr);
  if (!template.length) {
    if (!day.timeline) day.timeline = [];
    return day.timeline; // 计划外日期（假期/无计划）保持原样
  }
  const stored = dedupeBlocks(day.timeline || []);
  const byId = {};
  stored.forEach(function (b) { byId[b.id] = b; });

  const merged = template.map(function (tb) {
    const old = byId[tb.id];
    if (!old) return tb;
    if (old.customEdit) {
      return Object.assign({}, tb, {
        start: old.start, end: old.end, name: old.name, note: old.note, customEdit: true,
        done: !!old.done, rating: old.rating || 0, actualMinutes: old.actualMinutes || 0,
      }, mergeSubtaskState(tb, old));
    }
    return Object.assign({}, tb, {
      done: !!old.done,
      rating: old.rating || 0,
      actualMinutes: old.actualMinutes || 0,
    }, mergeSubtaskState(tb, old));
  });
  // 模板中已取消、但用户手工编辑过的块，继续保留在当天
  const templateIds = {};
  template.forEach(function (b) { templateIds[b.id] = true; });
  stored.forEach(function (b) {
    if (b.customEdit && !templateIds[b.id]) merged.push(b);
  });

  day.timeline = merged;
  return merged;
}

/* 子任务状态合并：按 key 保留打卡/评分/时长；旧数据无子任务时，原任务已打勾则子任务全部勾上 */
function mergeSubtaskState(tb, old) {
  if (!tb.subtasks) return {};
  let subs = tb.subtasks;
  if (old.subtasks) {
    subs = tb.subtasks.map(function (s) {
      const o = old.subtasks.filter(function (x) { return x.key === s.key; })[0];
      return o ? Object.assign({}, s, { done: !!o.done, rating: o.rating || 0, actualMinutes: o.actualMinutes || 0 }) : s;
    });
  } else if (old.done) {
    subs = tb.subtasks.map(function (s) { return Object.assign({}, s, { done: true }); });
  }
  const sum = subs.reduce(function (a, s) { return a + (s.actualMinutes || 0); }, 0);
  const legacyMin = old.subtasks ? 0 : (old.actualMinutes || 0);
  return { subtasks: subs, done: subs.every(function (s) { return s.done; }), actualMinutes: sum + legacyMin };
}

/* ---------- 星级评分 HTML ---------- */
function renderStars(blockId, rating) {
  let s = '';
  for (let i = 1; i <= 5; i++) {
    s += '<span class="star' + (i <= rating ? ' active' : '') + '" data-tlrate="' + blockId + '" data-val="' + i + '">★</span>';
  }
  return s;
}

/* 时间轴当前查看日期（可前一天/后一天/跳转；进入"今日"页时重置为今天） */
let timelineViewDate = todayStr();
function renderTimelineNav() {
  const pick = document.getElementById('tlDatePick');
  const back = document.getElementById('tlBackToday');
  if (pick) pick.value = timelineViewDate;
  if (back) back.hidden = (timelineViewDate === todayStr());
}
function setTimelineViewDate(dateStr) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return;
  timelineViewDate = dateStr;
  const state = loadState();
  renderTimeline(state, dateStr);
}
/* 打卡/评分/时长事件：来自 #timeline 操作"当前查看日期"，其余容器（任务清单页）操作今天 */
function eventViewDate(e) {
  return (e.target && e.target.closest && e.target.closest('#timeline')) ? timelineViewDate : todayStr();
}

function renderTimeline(state, dateStr) {
  const day = ensureDay(state, dateStr);
  const isToday = dateStr === todayStr();
  // 日期导航与标题日期保持同步
  renderTimelineNav();
  // 六级倒计时小卡片
  renderCet6Alert(dateStr);
  // 功能 F：每日追加"错题本复习"块（不写入 day.timeline，每次渲染从 errorBook 抽取）
  const blocks = ensureTimeline(state, dateStr).concat(getDailyErrorReview(state, dateStr));
  const pool = day.pendingPool || [];
  const now = nowTime();
  const proc = (state.procrastination && state.procrastination.counts) || {};

  // 渲染列表 = 待办池任务（最上方，优先级最高）+ 时间轴任务
  const renderList = [];
  pool.forEach(function (b) { renderList.push({ block: b, isPending: true }); });
  blocks.forEach(function (b) { renderList.push({ block: b, isPending: false }); });

  const total = renderList.length;
  const doneCount = renderList.filter(function (r) { return r.block.done; }).length;
  // 非今天：标题显示所查看的日期 + 过去/未来标记
  let goalTitle;
  if (isToday) {
    goalTitle = '今日核心目标';
  } else {
    const tag = dateStr < todayStr() ? '（历史记录，可补打卡）' : '（未来计划预览）';
    goalTitle = escapeHtml(dateStr.slice(5).replace('-', '/') + ' ' + (WEEKDAY_NAMES[getWeekday(dateStr)] || '')) + ' 任务目标' + tag;
  }
  document.getElementById('timelineGoal').innerHTML =
    goalTitle + '：完成 <b>' + total + '</b> 个任务 · 已完成 <b>' + doneCount + '</b> / ' + total +
    (pool.length ? ' · 待办池 <b class="pool-count">' + pool.length + '</b>' : '');

  document.getElementById('timeline').innerHTML = renderList.map(function (r) {
    const b = r.block, isPending = r.isPending;
    // 每日错题复习：纯提示块，带"开始复习"入口
    if (b.id && b.id.indexOf('eb-review-') === 0) {
      return '<div class="tl-item info eb-review" data-id="' + b.id + '">' +
        '<div class="tl-row1">' +
          '<div class="tl-time">📓 每日复习</div>' +
          '<div class="tl-body">' +
            '<div class="tl-name">' + escapeHtml(b.name) + '</div>' +
            (b.note ? '<div class="tl-note">' + escapeHtml(b.note) + '</div>' : '') +
          '</div>' +
          '<div class="tl-actions"><button class="tl-focus-btn" data-tlfocus-review="1">▶ 开始复习</button></div>' +
        '</div>' +
      '</div>';
    }
    // 通用信息块（政治课"上课"标记 / 六级考试日等）：无打卡、无星级
    if (b.type === 'info') {
      const icon = b.subject === 'class' ? '🏫' : (b.subject === 'cet6' ? '🎯' : 'ℹ️');
      return '<div class="tl-item info subj-' + (b.subject || 'info') + '" data-id="' + b.id + '">' +
        '<div class="tl-row1">' +
          '<div class="tl-time">' + (b.start ? (b.start + ' - ' + b.end) : icon + ' 信息') + '</div>' +
          '<div class="tl-body">' +
            '<div class="tl-name">' + icon + ' ' + escapeHtml(b.name) + '</div>' +
            (b.note ? '<div class="tl-note">' + escapeHtml(b.note) + '</div>' : '') +
          '</div>' +
        '</div>' +
      '</div>';
    }
    const procCount = b.subject ? (proc[b.subject] || 0) : 0;
    const severe = procCount > 3;
    // 超时样式只对"今天"生效，避免查看历史日期时所有未打卡任务都标红
    const overdue = isToday && !b.done && !isPending && b.end && now > b.end;

    let cls = (isPending ? 'pending' : (b.type || 'task')) + (b.subject ? ' subj-' + b.subject : '');
    if (b.done) cls += ' done';
    if (overdue) cls += ' overdue';
    // 正在专注的任务高亮
    const focusing = pomo.running && pomo.mode === 'focus' && pomo.taskId === b.id;
    if (focusing) cls += ' focusing';

    const timeText = isPending ? '📌 待办池' : (b.start ? (b.start + ' - ' + b.end) : '自由时间');
    let noteParts = [];
    if (isPending && b.from) noteParts.push('来自 ' + formatDateCN(b.from));
    if (severe) noteParts.push('<span class="severe">⚠️ 严重拖延</span>');
    else if (procCount > 0 && b.subject) noteParts.push('拖延 ' + procCount + ' 次');
    if (b.note) noteParts.push(escapeHtml(b.note));
    const noteHtml = noteParts.length ? '<div class="tl-note">' + noteParts.join(' · ') + '</div>' : '';

    return '<div class="tl-item ' + cls + '" data-id="' + b.id + '">' +
      '<div class="tl-row1">' +
        '<div class="tl-time">' + timeText + '</div>' +
        '<div class="tl-body">' +
          '<div class="tl-name">' + escapeHtml(b.name) + '</div>' +
          noteHtml +
        '</div>' +
        '<label class="tl-check" title="任务打卡（联动子任务）">' +
          '<input type="checkbox" data-tldone="' + b.id + '"' + (b.done ? ' checked' : '') + ' aria-label="任务打卡">' +
        '</label>' +
      '</div>' +
      '<div class="tl-row2">' +
        '<div class="tl-rating">' + renderStars(b.id, b.rating || 0) + '</div>' +
        (b.subtasks && b.subtasks.length
          ? '<span class="tl-min-label">有效时长 <b>' + (b.actualMinutes || 0) + '</b> 分钟（子项合计）</span>'
          : '<label class="tl-min-label">有效时长 <input type="number" class="tl-minutes" data-tlmin="' + b.id + '" min="0" max="600" value="' + (b.actualMinutes || '') + '" placeholder="0" inputmode="numeric"> 分钟</label>') +
        (isToday
          ? '<button class="tl-focus-btn" data-tlfocus="' + b.id + '"' + (focusing ? ' disabled' : '') + '>' + (focusing ? '⏹ 专注中' : '▶ 开始专注') + '</button>'
          : '<button class="tl-focus-btn" disabled title="只有当天的任务可以启动专注">🔒 非当天</button>') +
      '</div>' +
      renderSubtaskGroup(b) +
    '</div>';
  }).join('');

  // 空状态：该日期无任务（计划外日期/假期）
  if (renderList.length === 0) {
    document.getElementById('timeline').innerHTML =
      '<p class="empty">当天暂无任务安排' + (isToday ? '' : '（' + escapeHtml(dateStr) + '）') + '</p>';
  }

  // 功能 F：渲染错题本输入区
  renderErrorBook(state);
}

/* ---------- 「我的」页面：本周拖延统计 ---------- */
function renderProcrastinationStats(state) {
  const el = document.getElementById('procrastinationStats');
  if (!el) return;
  const proc = state.procrastination || { week: getWeekNumber(todayStr()), counts: {} };
  const counts = proc.counts || {};

  let maxSubject = '', maxCount = 0;
  Object.keys(counts).forEach(function (k) {
    if (counts[k] > maxCount) { maxCount = counts[k]; maxSubject = k; }
  });

  let html = '<div class="proc-week">第 ' + proc.week + ' 周</div>';
  if (maxSubject) {
    html += '<div class="proc-top">本周最常拖延的科目：<b>' + escapeHtml(SUBJECT_LABELS[maxSubject] || maxSubject) + '</b>（拖延 ' + maxCount + ' 次）</div>';
  } else {
    html += '<div class="proc-top">本周暂无拖延记录，继续保持！</div>';
  }

  html += '<div class="proc-list">';
  ['math', 'ds', 'cs', 'eng'].forEach(function (s) {
    const count = counts[s] || 0;
    const severe = count > 3;
    html += '<div class="proc-item' + (severe ? ' severe' : '') + '">' +
      '<span class="proc-name">' + escapeHtml(SUBJECT_LABELS[s] || s) + '</span>' +
      '<span class="proc-count">' + count + ' 次' + (severe ? ' · ⚠️ 严重拖延' : '') + '</span>' +
    '</div>';
  });
  html += '</div>';
  el.innerHTML = html;
}

/* ---------- 功能 F：错题本 2.0（照片 + 复习模式 + 每日队列） ---------- */
function getErrorBook(state) {
  state.errorBook = state.errorBook || [];
  // 旧数据迁移：补字段
  state.errorBook.forEach(function (e) {
    if (e.mastered === undefined) e.mastered = !!e.done;
    if (e.reviewCount === undefined) e.reviewCount = 0;
    if (e.lastReviewDate === undefined) e.lastReviewDate = '';
    if (e.photo === undefined) e.photo = '';
    delete e.done; // 旧字段清理（保留 mastered）
  });
  return state.errorBook;
}

/* 今日待复习队列：未掌握 + （上次复习不在今天或为空），最多 5 条 */
function getTodayReviewQueue(state, dateStr) {
  const eb = getErrorBook(state);
  const today = dateStr;
  const candidates = eb.filter(function (e) {
    return !e.mastered && e.lastReviewDate !== today;
  });
  // 优先连续没掌握次数多的（重点攻克），同序按最近加入
  candidates.sort(function (a, b) { return (b.reviewCount || 0) - (a.reviewCount || 0); });
  return candidates.slice(0, 5);
}

function renderErrorBook(state) {
  const el = document.getElementById('errorBook');
  if (!el) return;
  const eb = getErrorBook(state);
  const queue = getTodayReviewQueue(state, todayStr());
  const queueCount = queue.length;

  const list = eb.length ? eb.map(function (e, i) {
    const severe = (e.reviewCount || 0) >= 3 && !e.mastered;
    return '<div class="eb-item' + (e.mastered ? ' done' : '') + (severe ? ' severe' : '') + '">' +
      (e.photo ? '<img class="eb-thumb" src="' + e.photo + '" alt="错题照片" data-ebphoto="' + i + '">' : '') +
      '<span class="eb-date">' + (e.date || '').slice(5) + '</span>' +
      '<div class="eb-body">' +
        '<span class="eb-text">' + escapeHtml(e.text || '') + '</span>' +
        (e.reviewCount ? '<span class="eb-rc">复习 ' + e.reviewCount + ' 次</span>' : '') +
        (severe ? '<span class="eb-flag">⚠️ 重点攻克</span>' : '') +
      '</div>' +
      '<button class="eb-toggle" data-ebtoggle="' + i + '" title="标记已掌握">' + (e.mastered ? '↩' : '✓') + '</button>' +
      '<button class="eb-del" data-ebdel="' + i + '" title="删除">✕</button>' +
    '</div>';
  }).join('') : '<p class="empty">错题本为空，输入一个没搞懂的知识点或拍照加入吧～</p>';

  el.innerHTML =
    '<div class="eb-title">📓 错题本 2.0</div>' +
    '<div class="eb-input-row">' +
      '<input type="text" id="ebInput" placeholder="如：泰勒公式没搞懂（回车加入）" />' +
      '<label class="eb-photo-btn" title="上传/拍照">📷<input type="file" id="ebPhoto" accept="image/*" capture="environment" hidden></label>' +
      '<button class="btn btn-primary btn-sm" id="ebAddBtn">加入</button>' +
    '</div>' +
    '<div class="eb-preview" id="ebPreview" hidden></div>' +
    (queueCount ?
      '<div class="eb-review-entry">' +
        '<span>今日待复习：' + queueCount + ' 道</span>' +
        '<button class="btn btn-primary btn-sm" id="ebReviewBtn">▶ 开始复习</button>' +
      '</div>' : '') +
    '<div class="eb-list">' + list + '</div>' +
    '<p class="hint">每日自动从未掌握中挑 3-5 道进入今日时间轴 · 连续 3 次没掌握将红色高亮提示重点攻克</p>';
}

/* 每日时间轴：插入"错题本复习"任务块（每天最多 1 条） */
function getDailyErrorReview(state, dateStr) {
  const queue = getTodayReviewQueue(state, dateStr);
  if (queue.length === 0) return [];
  return [{
    id: 'eb-review-' + dateStr,
    type: 'review',
    subject: 'review',
    name: '错题本复习（今日 ' + queue.length + ' 道）',
    note: '点击右侧"▶ 开始复习"进入卡片模式',
    start: '', end: '',
    done: false, rating: 0, actualMinutes: 0,
  }];
}

/* ---------- 错题本复习模式（全屏卡片） ---------- */
let ebReviewIdx = 0;
let ebReviewQueue = [];
function openEbReviewMode(state) {
  ebReviewQueue = getTodayReviewQueue(state, todayStr());
  ebReviewIdx = 0;
  if (ebReviewQueue.length === 0) {
    alert('今日没有待复习的错题，继续加油！');
    return;
  }
  renderEbReviewCard();
  const ov = document.getElementById('ebReviewOverlay');
  ov.hidden = false;
  ov.style.display = 'flex'; // 双保险：防止旧 CSS 缓存导致 [hidden] 失效
}
function closeEbReviewMode() {
  const ov = document.getElementById('ebReviewOverlay');
  ov.hidden = true;
  ov.style.display = 'none'; // 双保险：强制隐藏
}
function renderEbReviewCard() {
  if (ebReviewIdx < 0) ebReviewIdx = 0;
  if (ebReviewIdx >= ebReviewQueue.length) ebReviewIdx = ebReviewQueue.length - 1;
  const e = ebReviewQueue[ebReviewIdx];
  if (!e) return;
  document.getElementById('ebReviewCard').innerHTML =
    '<div class="eb-card-progress">第 ' + (ebReviewIdx + 1) + ' / ' + ebReviewQueue.length + ' 题</div>' +
    '<div class="eb-card-body">' +
      (e.photo ? '<img class="eb-card-img" src="' + e.photo + '" alt="错题照片">' : '<div class="eb-card-noimg">📷 无照片</div>') +
      '<div class="eb-card-text">' + escapeHtml(e.text || '(无文字)') + '</div>' +
      '<div class="eb-card-meta">已复习 ' + (e.reviewCount || 0) + ' 次 · 加入于 ' + formatDateCN(e.date || todayStr()) + '</div>' +
    '</div>' +
    '<div class="eb-card-actions">' +
      '<button class="btn btn-ghost" data-ebnav="prev"' + (ebReviewIdx === 0 ? ' disabled' : '') + '>◀ 上一题</button>' +
      '<button class="btn btn-primary" data-ebmaster="yes">✅ 今日已复习，掌握</button>' +
      '<button class="btn btn-ghost danger" data-ebmaster="no">🤔 不太理解</button>' +
      '<button class="btn btn-ghost" data-ebnav="next"' + (ebReviewIdx >= ebReviewQueue.length - 1 ? ' disabled' : '') + '>下一题 ▶</button>' +
    '</div>';
}

function openEditModal(b) {
  document.getElementById('modalContent').innerHTML =
    '<h3>✏️ 修改时间块</h3>' +
    '<div class="edit-form">' +
      '<label>名称</label><input id="editName" value="' + escapeHtml(b.name) + '">' +
      '<label>备注</label><input id="editNote" value="' + escapeHtml(b.note || '') + '">' +
      '<div class="row2">' +
        '<div><label>开始</label><input id="editStart" type="time" value="' + (b.start || '') + '"></div>' +
        '<div><label>结束</label><input id="editEnd" type="time" value="' + (b.end || '') + '"></div>' +
      '</div>' +
    '</div>' +
    '<div class="modal-actions">' +
      '<button class="btn btn-primary" onclick="saveTimelineEdit()">保存</button>' +
      '<button class="btn btn-ghost" onclick="closeModal()">取消</button>' +
    '</div>';
  openModal();
}

function saveTimelineEdit() {
  const name = document.getElementById('editName').value.trim();
  const note = document.getElementById('editNote').value.trim();
  const start = document.getElementById('editStart').value;
  const end = document.getElementById('editEnd').value;
  if (!name) { alert('名称不能为空'); return; }
  const state = loadState();
  ensureDay(state, todayStr());
  ensureTimeline(state, todayStr());
  const b = findTimelineBlock(state, todayStr(), editingBlock.id);
  if (b) { b.name = name; b.note = note; if (start) b.start = start; if (end) b.end = end; b.customEdit = true; }
  saveState(state);
  closeModal();
  renderTimeline(state, todayStr());
}

/* ---------- 渲染：完整课表网格 ---------- */
// 课表周次限定在第 1-20 教学周；开学前（周次<1）默认展示第 1 周
function clampScheduleWeek(w) {
  if (w < 1) return 1;
  if (w > 20) return 20;
  return w;
}
let scheduleViewWeek = clampScheduleWeek(getWeekNumber(todayStr()));
let selectedWeekday = getWeekday(todayStr());

/* ---------- 课表页：星期切换栏 + 当日课程卡片 ---------- */
function renderWeekdayTabs() {
  const el = document.getElementById('weekdayTabs');
  if (!el) return;
  const wdToday = getWeekday(todayStr());
  el.innerHTML = WEEKDAY_NAMES.slice(1).map(function (name, idx) {
    const wd = idx + 1;
    const isToday = wd === wdToday;
    const isActive = wd === selectedWeekday;
    return '<button class="weekday-tab' + (isActive ? ' active' : '') + (isToday ? ' is-today' : '') + '" data-wd="' + wd + '">' + name + '</button>';
  }).join('');
}

function renderDayCourses() {
  const el = document.getElementById('dayCourses');
  if (!el) return;
  const wd = selectedWeekday;
  const week = scheduleViewWeek;
  const courses = (SCHEDULE[wd] || []).filter(function (c) {
    return parseWeeks(c.weeks).has(week);
  }).sort(function (a, b) { return a.period - b.period; });

  if (courses.length === 0) {
    el.innerHTML = '<p class="empty">' + WEEKDAY_NAMES[wd] + '（第 ' + week + ' 周）无课程安排 🎉</p>';
    return;
  }
  el.innerHTML = courses.map(function (c) {
    const p = PERIODS.find(function (x) { return x.period === c.period; });
    return '<div class="day-course-card">' +
      '<div class="dcc-top">' +
        '<span class="dcc-period">' + (p ? p.name : '第' + c.period + '大节') + '</span>' +
        '<span class="dcc-time">' + (p ? p.start + ' - ' + p.end : '') + '</span>' +
      '</div>' +
      '<div class="dcc-name">' + escapeHtml(c.name) + '</div>' +
      '<div class="dcc-meta">' +
        (c.location ? '<span>📍 ' + escapeHtml(c.location) + '</span>' : '') +
        '<span>📆 第 ' + c.weeks + ' 周</span>' +
        (c.teacher ? '<span>👨‍🏫 ' + escapeHtml(c.teacher) + '</span>' : '') +
      '</div>' +
    '</div>';
  }).join('');
}

function renderScheduleGrid(state) {
  const today = todayStr();
  const currentWeek = getWeekNumber(today);
  const week = scheduleViewWeek;
  const wdToday = getWeekday(today);
  let label = '第 ' + week + ' 周';
  if (week === currentWeek) label += '（本周）';
  else if (currentWeek < 1) label += '（学期 ' + SEMESTER_START.slice(5) + ' 开始）';
  document.getElementById('weekLabel').textContent = label;

  let html = '<div class="schedule-scroll"><table class="schedule"><thead><tr><th class="corner"></th>';
  for (let wd = 1; wd <= 5; wd++) {
    const isToday = (week === currentWeek && wd === wdToday);
    html += '<th class="' + (isToday ? 'today' : '') + '">' + WEEKDAY_NAMES[wd] + '</th>';
  }
  html += '</tr></thead><tbody>';

  // 只渲染本周有课的大节；无课的大节整行不显示
  let renderedRows = 0;
  PERIODS.forEach(function (p) {
    // 先统计这一行（大节）在本周是否有任何课程
    let hasAnyCourse = false;
    for (let wd = 1; wd <= 5; wd++) {
      const list = (SCHEDULE[wd] || []).filter(function (c) {
        return c.period === p.period && parseWeeks(c.weeks).has(week);
      });
      if (list.length) { hasAnyCourse = true; break; }
    }
    if (!hasAnyCourse) return; // 本周该大节无课，跳过整行
    renderedRows++;

    html += '<tr><td class="period">' + p.name + '</td>';
    for (let wd = 1; wd <= 5; wd++) {
      const isToday = (week === currentWeek && wd === wdToday);
      // 关键修正：单元格只显示该大节、该周、该星期的课程
      const courses = (SCHEDULE[wd] || []).filter(function (c) {
        return c.period === p.period && parseWeeks(c.weeks).has(week);
      });
      const cell = courses.map(function (c) {
        return '<div class="sc-course"><span class="sc-name">' + escapeHtml(c.name) + '</span>' +
          (c.location ? '<span class="sc-loc">' + escapeHtml(c.location) + '</span>' : '') + '</div>';
      }).join('');
      html += '<td class="' + (isToday ? 'today' : '') + '">' + cell + '</td>';
    }
    html += '</tr>';
  });

  if (!renderedRows) {
    html += '<tr><td colspan="6" class="schedule-empty">本周（第 ' + week + ' 周）没有课程安排</td></tr>';
  }
  html += '</tbody></table></div>';
  document.getElementById('scheduleGrid').innerHTML = html;
}

/* ---------- 渲染：统计图表 ---------- */
let chartInstance = null;

function renderChart(state) {
  const days = [];
  for (let i = 6; i >= 0; i--) days.push(addDays(todayStr(), -i));
  const labels = days.map(function (d) { return d.slice(5); }); // MM-DD
  const mathData = days.map(function (d) { return (state.days[d] && state.days[d].problems) ? state.days[d].problems.math : 0; });
  const csData = days.map(function (d) { return (state.days[d] && state.days[d].problems) ? state.days[d].problems.cs : 0; });

  const canvas = document.getElementById('chart');
  const fallback = document.getElementById('chartFallback');

  if (window.Chart) {
    fallback.innerHTML = '';
    fallback.style.display = 'none';
    canvas.style.display = 'block';
    if (chartInstance) chartInstance.destroy();
    chartInstance = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          { label: '数学', data: mathData, backgroundColor: '#5b7fd0', borderRadius: 6 },
          { label: '408', data: csData, backgroundColor: '#4c9f6b', borderRadius: 6 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  } else {
    canvas.style.display = 'none';
    fallback.style.display = 'block';
    const max = Math.max(1, mathData.concat(csData).reduce(function (a, b) { return Math.max(a, b); }, 0));
    fallback.innerHTML = '<div class="fb-legend"><span><i class="dot math"></i>数学</span><span><i class="dot cs"></i>408</span></div>' +
      days.map(function (d, i) {
        return '<div class="fb-row"><span class="fb-label">' + labels[i] + '</span>' +
          '<div class="fb-bars">' +
            '<div class="fb-bar fb-math" style="width:' + (mathData[i] / max * 100) + '%"></div>' +
            '<div class="fb-bar fb-cs" style="width:' + (csData[i] / max * 100) + '%"></div>' +
          '</div>' +
          '<span class="fb-val">' + mathData[i] + '/' + csData[i] + '</span></div>';
      }).join('');
  }
}

/* ---------- 渲染：本周战报 ---------- */
function getWeekMonday(dateStr) { return addDays(dateStr, -(getWeekday(dateStr) - 1)); }
function getWeekRange() {
  const monday = getWeekMonday(todayStr());
  const days = [];
  for (let i = 0; i < 7; i++) days.push(addDays(monday, i));
  return days;
}

function computeWeekStats(state) {
  const days = getWeekRange();
  let pomos = 0, words = 0, probs = 0, done = 0, total = 0;
  days.forEach(function (d) {
    const day = state.days && state.days[d];
    if (!day) return;
    pomos += day.pomodoros || 0;
    words += (day.words && day.words.newCount) || 0;
    probs += ((day.problems && day.problems.math) || 0) + ((day.problems && day.problems.cs) || 0);
    const items = getDayItems(state, d);
    total += items.length;
    done += items.filter(function (i) { return i.done; }).length;
  });
  return { focusMin: pomos * POMO_FOCUS_MIN, words: words, probs: probs, rate: total ? Math.round(done / total * 100) : 0, days: days };
}

function buildReportHTML(stats) {
  return '<div class="report">' +
    '<div class="report-title">📚 考研学习 · 周战报</div>' +
    '<div class="report-range">' + stats.days[0] + ' ~ ' + stats.days[6] + '</div>' +
    '<div class="report-grid">' +
      '<div class="report-stat"><div class="rv">' + fmtMinutes(stats.focusMin) + '</div><div class="rl">本周专注</div></div>' +
      '<div class="report-stat"><div class="rv">' + stats.words + '</div><div class="rl">新学单词</div></div>' +
      '<div class="report-stat"><div class="rv">' + stats.probs + '</div><div class="rl">刷题总数</div></div>' +
      '<div class="report-stat"><div class="rv">' + stats.rate + '%</div><div class="rl">任务完成率</div></div>' +
    '</div>' +
    '<div class="report-foot">坚持就是胜利，岸上见！💪</div>' +
  '</div>';
}

function generateReport() {
  const state = loadState();
  const stats = computeWeekStats(state);
  document.getElementById('reportRoot').innerHTML = buildReportHTML(stats);
  if (window.html2canvas) {
    html2canvas(document.getElementById('reportRoot'), { scale: 2, backgroundColor: '#ffffff', useCORS: true })
      .then(function (canvas) { showModalWithImage(canvas); })
      .catch(function () { showModalWithHTML(stats); });
  } else {
    showModalWithHTML(stats);
  }
}

function showModalWithImage(canvas) {
  const url = canvas.toDataURL('image/png');
  document.getElementById('modalContent').innerHTML =
    '<h3>📊 本周战报</h3>' +
    '<img class="report-img" src="' + url + '" alt="本周战报">' +
    '<div class="modal-actions">' +
      '<a class="btn btn-primary" href="' + url + '" download="weekly-report-' + todayStr() + '.png">保存图片</a>' +
      '<button class="btn btn-ghost" onclick="closeModal()">关闭</button>' +
    '</div>';
  openModal();
}

function showModalWithHTML(stats) {
  document.getElementById('modalContent').innerHTML =
    '<h3>📊 本周战报</h3>' +
    '<div class="report-inline">' + buildReportHTML(stats) + '</div>' +
    '<p class="hint">图片生成库（html2canvas）未加载，联网后重试即可生成图片；当前可先截图保存。</p>' +
    '<div class="modal-actions"><button class="btn btn-primary" onclick="closeModal()">知道了</button></div>';
  openModal();
}

function openModal() { document.getElementById('modalOverlay').hidden = false; }
function closeModal() { document.getElementById('modalOverlay').hidden = true; }

/* ---------- 渲染：我的 ---------- */
/* 单词设置三项：每日新词 / 每日复习 / 大纲总词量（存 localStorage，随 state 持久化） */
const WORD_DEFAULT_NEW = 50;
const WORD_DEFAULT_REVIEW = 100;
function getWordSettings(state) {
  const ws = state.wordSettings || {};
  return {
    dailyNew: Math.max(1, parseInt(ws.dailyNew, 10) || WORD_DEFAULT_NEW),
    dailyReview: Math.max(0, parseInt(ws.dailyReview, 10) === 0 ? 0 : (parseInt(ws.dailyReview, 10) || WORD_DEFAULT_REVIEW)),
    target: Math.max(1, parseInt(ws.target, 10) || state.wordGoal || WORD_TARGET),
  };
}
function getWordTarget(state) {
  return getWordSettings(state).target;
}
/* 「我的」页：单词设置表单 */
function wordSettingsHtml(state) {
  const ws = getWordSettings(state);
  const daysForOneRound = Math.ceil(ws.target / ws.dailyNew);
  return '<div class="word-plan-chips">' +
      '<span>每日新词 ' + ws.dailyNew + ' 个</span>' +
      '<span>每日复习 ' + ws.dailyReview + ' 个</span>' +
      '<span>大纲总量 ' + ws.target + ' 个</span>' +
    '</div>' +
    '<div class="word-settings">' +
      '<label>每日计划背新词数 <input type="number" class="js-ws-new" min="1" max="500" value="' + ws.dailyNew + '" inputmode="numeric"> 个</label>' +
      '<label>每日计划复习旧词数 <input type="number" class="js-ws-review" min="0" max="2000" value="' + ws.dailyReview + '" inputmode="numeric"> 个</label>' +
      '<label>考研大纲总词量 <input type="number" class="js-ws-target" min="500" max="20000" value="' + ws.target + '" inputmode="numeric"> 个</label>' +
      '<button class="btn btn-primary btn-sm js-wsave-btn" type="button">保存单词设置</button>' +
    '</div>' +
    '<p class="hint">按每日新词 ' + ws.dailyNew + ' 个计算，约 <b>' + daysForOneRound + '</b> 天可背完一轮大纲词。</p>';
}
function renderWordGoal(state) {
  const el = document.getElementById('wordGoal');
  if (el) el.innerHTML = wordSettingsHtml(state);
}
/* 「统计」页：累计 / 剩余 / 今日新词+复习进度 / 总进度落后提醒 */
function wordStatsHtml(state) {
  const ws = getWordSettings(state);
  const target = ws.target;
  const today = todayStr();
  const day = state.days && state.days[today];
  const newDone = (day && day.words && day.words.newCount) || 0;
  const reviewDone = (day && day.words && day.words.reviewCount) || 0;
  const newLeft = Math.max(0, ws.dailyNew - newDone);
  const reviewLeft = Math.max(0, ws.dailyReview - reviewDone);
  const newLag = newDone < ws.dailyNew;
  const reviewLag = reviewDone < ws.dailyReview;

  const total = getTotalLearned(state);
  const remain = Math.max(0, target - total);
  const pct = Math.min(100, Math.round(total / target * 100));
  const examDate = getExamDate(state);
  const daysLeft = Math.max(0, daysUntil(examDate));
  // 时间进度（开学到考试），判断总进度是否落后
  const totalDays = Math.round((parseDate(examDate) - parseDate(SEMESTER_START)) / 86400000) + 1;
  const elapsedDays = Math.round((parseDate(today) - parseDate(SEMESTER_START)) / 86400000) + 1;
  const timePct = Math.min(100, Math.max(0, Math.round(elapsedDays / totalDays * 100)));
  const behind = pct < timePct;

  function todayRow(title, done, goal, left, lag, unit) {
    const p = goal > 0 ? Math.min(100, Math.round(done / goal * 100)) : 100;
    return '<div class="wt-row">' +
      '<div class="wt-head"><span>' + title + '</span><b>' + done + ' / ' + goal + ' ' + unit + '</b></div>' +
      '<div class="wt-bar"><div class="wt-fill' + (lag ? ' lag' : '') + '" style="width:' + p + '%"></div></div>' +
      (lag ? '<div class="wt-warn">⚠️ 今日还差 <b>' + left + '</b> ' + unit + '，进度落后</div>'
           : '<div class="wt-ok">✅ 今日' + title + '目标已达成</div>') +
    '</div>';
  }

  return '<div class="progress-head"><span>大纲总进度</span>' +
      '<span class="progress-pct">' + total + ' / ' + target + ' · ' + pct + '%</span>' +
    '</div>' +
    '<div class="bar' + (behind ? ' bar-lag' : '') + '"><div class="bar-fill fill-word' + (behind ? ' fill-lag' : '') + '" style="width:' + pct + '%"></div>' +
      '<div class="bar-time-marker" style="left:' + timePct + '%"></div>' +
    '</div>' +
    (behind ? '<div class="word-lag-warn">⚠️ 距离考研还有 <b>' + daysLeft + '</b> 天，词汇总进度落后时间进度 ' + (timePct - pct) + '%，请适当增加每日新词量</div>'
            : '<div class="wt-ok">✅ 距考研 ' + daysLeft + ' 天，总进度正常</div>') +
    '<ul class="goal-list">' +
      '<li>累计已背：<b>' + total + '</b> 个（总目标 ' + pct + '%）</li>' +
      '<li>还差单词：<b>' + remain + '</b> 个</li>' +
      '<li>距初试：<b>' + daysLeft + '</b> 天</li>' +
    '</ul>' +
    todayRow('今日新词', newDone, ws.dailyNew, newLeft, newLag, '个') +
    todayRow('今日复习', reviewDone, ws.dailyReview, reviewLeft, reviewLag, '个');
}

function renderStudyTime() {
  const state = loadState();
  const day = state.days && state.days[todayStr()];
  const min = (day && day.studyMinutes) || 0;
  document.getElementById('studyTime').innerHTML =
    '今日已学习 <b>' + fmtMinutes(min) + '</b>（番茄钟 ' + ((day && day.pomodoros) || 0) + ' 个）';
}

/* ---------- 番茄钟（升级版：自定义时长 + 任务联动 + 统计） ---------- */
const POMO_PREF_KEY = 'kaoyan_pomo_pref';
function loadPomoPref() {
  try {
    const p = JSON.parse(localStorage.getItem(POMO_PREF_KEY)) || {};
    return { focusMin: p.focusMin || 25, breakMin: (p.breakMin === 0 ? 0 : (p.breakMin || 5)) };
  } catch (e) { return { focusMin: 25, breakMin: 5 }; }
}
function savePomoPref(pref) {
  try { localStorage.setItem(POMO_PREF_KEY, JSON.stringify(pref)); } catch (e) {}
}
let pomoPref = loadPomoPref();

const pomo = {
  mode: 'focus', running: false, remaining: pomoPref.focusMin * 60,
  endAt: null, timer: null,
  // 任务联动：专注时绑定的 timeline blockId + 任务名
  taskId: null, taskName: '', plannedMinutes: 0,
  // 本次专注会话开始时间戳（用于专注记录的起止时刻）
  sessionStartTs: null,
  // 历史最长/今日最长记录
  sessionMinutes: 0,
};

function pomoTotal() {
  const m = pomo.mode === 'focus' ? pomoPref.focusMin : pomoPref.breakMin;
  return m * 60;
}
function fmtSec(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}
function getTodayPomodoros() {
  const state = loadState();
  const day = state.days && state.days[todayStr()];
  return (day && day.pomodoros) || 0;
}
/* 今日专注总分钟数 = 各 timeline block 的 actualMinutes + pomo.sessionMinutes 兜底 */
function getTodayFocusMinutes(state) {
  const day = state.days && state.days[todayStr()];
  let sum = (day && day.studyMinutes) || 0;
  if (day && day.timeline) {
    day.timeline.forEach(function (b) { sum += (b.actualMinutes || 0); });
  }
  if (day && day.pendingPool) {
    day.pendingPool.forEach(function (b) { sum += (b.actualMinutes || 0); });
  }
  return sum;
}
function getTodayMaxFocus(state) {
  const day = state.days && state.days[todayStr()];
  let mx = 0;
  if (day && day.timeline) day.timeline.forEach(function (b) { if ((b.actualMinutes || 0) > mx) mx = b.actualMinutes; });
  if (day && day.pendingPool) day.pendingPool.forEach(function (b) { if ((b.actualMinutes || 0) > mx) mx = b.actualMinutes; });
  return mx;
}
function getWeekFocusMinutes(state) {
  let total = 0;
  for (let i = 0; i < 7; i++) {
    const d = addDays(todayStr(), -i);
    const day = state.days && state.days[d];
    if (!day) continue;
    total += day.studyMinutes || 0;
    if (day.timeline) day.timeline.forEach(function (b) { total += (b.actualMinutes || 0); });
    if (day.pendingPool) day.pendingPool.forEach(function (b) { total += (b.actualMinutes || 0); });
  }
  return total;
}

function renderPomo() {
  const modeEl = document.getElementById('pomoMode');
  modeEl.textContent = (pomo.mode === 'focus') ? '专注' : '休息';
  modeEl.className = 'pomo-mode ' + pomo.mode;
  document.getElementById('pomoTime').textContent = fmtSec(pomo.remaining);
  document.getElementById('pomoStart').textContent = pomo.running ? '暂停' : (pomo.remaining < pomoTotal() ? '继续' : '开始');
  const cur = pomo.taskName ? '当前专注：' + escapeHtml(pomo.taskName) : '';
  document.getElementById('pomoCurrentTask').innerHTML = cur;
  // 今日专注统计
  const state = loadState();
  const todayMin = getTodayFocusMinutes(state);
  const maxMin = getTodayMaxFocus(state);
  const weekMin = getWeekFocusMinutes(state);
  const wH = Math.floor(weekMin / 60), wM = weekMin % 60;
  document.getElementById('pomoCount').innerHTML =
    '今日已专注：<b>' + getTodayPomodoros() + '</b> 个番茄钟，共 <b>' + todayMin + '</b> 分钟<br>' +
    '今日最长专注：<b>' + maxMin + '</b> 分钟 · 本周累计：<b>' + wH + ' 小时 ' + wM + ' 分钟</b>';
  document.title = fmtSec(pomo.remaining) + ' ' + (pomo.mode === 'focus' ? '专注' : '休息') + (pomo.taskName ? ' · ' + pomo.taskName : '') + ' · 考研学习打卡';
}

function pomoPrefHtml() {
  const focusOpts = [25, 45, 60, 90];
  const breakOpts = [5, 10, 15, 0];
  return '<div class="pomo-pref-row"><span class="pomo-pref-label">专注时长</span>' +
      focusOpts.map(function (m) {
        const active = pomoPref.focusMin === m;
        return '<button class="pomo-chip' + (active ? ' active' : '') + '" data-pomofocus="' + m + '">' + m + '分</button>';
      }).join('') +
      '<input type="number" class="pomo-custom" data-pomofocuscustom min="1" max="180" value="' + (focusOpts.indexOf(pomoPref.focusMin) >= 0 ? '' : pomoPref.focusMin) + '" placeholder="自定义" inputmode="numeric"> 分' +
    '</div>' +
    '<div class="pomo-pref-row"><span class="pomo-pref-label">休息时长</span>' +
      breakOpts.map(function (m) {
        const active = pomoPref.breakMin === m;
        return '<button class="pomo-chip' + (active ? ' active' : '') + '" data-pomobreak="' + m + '">' + (m === 0 ? '不休息' : m + '分') + '</button>';
      }).join('') +
    '</div>';
}
// 同时渲染到今日番茄钟卡片 和 我的-设置（所有 .pomo-pref 挂载点）
function renderPomoPref() {
  document.querySelectorAll('.pomo-pref').forEach(function (el) { el.innerHTML = pomoPrefHtml(); });
}

function startCountdown() {
  pomo.running = true;
  pomo.endAt = Date.now() + pomo.remaining * 1000;
  if (pomo.mode === 'focus' && (pomo.remaining >= pomoTotal() || !pomo.sessionStartTs)) {
    pomo.sessionStartTs = Date.now();
  }
  if (pomo.timer) clearInterval(pomo.timer);
  pomo.timer = setInterval(tick, 500);
}
function pauseCountdown() {
  pomo.running = false;
  if (pomo.endAt) pomo.remaining = Math.max(0, Math.round((pomo.endAt - Date.now()) / 1000));
  pomo.endAt = null;
  if (pomo.timer) { clearInterval(pomo.timer); pomo.timer = null; }
}
function tick() {
  const left = Math.max(0, Math.round((pomo.endAt - Date.now()) / 1000));
  pomo.remaining = left;
  if (left <= 0) { onPomoComplete(); return; }
  renderPomo();
}
function onPomoComplete() {
  beep();
  if (navigator.vibrate) navigator.vibrate([200, 100, 200]); // 震动提醒
  if (pomo.mode === 'focus') {
    const state = loadState();
    const day = ensureDay(state, todayStr());
    day.pomodoros = (day.pomodoros || 0) + 1;
    const elapsedMin = pomoPref.focusMin;
    day.studyMinutes = (day.studyMinutes || 0) + elapsedMin;
    // 把专注时长写回关联的 timeline 任务，并记录一条专注日志
    let logSubject = '';
    if (pomo.taskId) {
      const b = findTimelineBlock(state, todayStr(), pomo.taskId);
      if (b) {
        b.actualMinutes = (b.actualMinutes || 0) + elapsedMin;
        logSubject = b.subject || '';
      }
    }
    day.focusLog = day.focusLog || [];
    const startTs = pomo.sessionStartTs || (Date.now() - elapsedMin * 60000);
    day.focusLog.push({
      id: 'fs-' + Date.now(),
      name: pomo.taskName || '自由专注',
      subject: logSubject,
      start: fmtClock(startTs),
      end: fmtClock(Date.now()),
      minutes: elapsedMin,
      ts: Date.now()
    });
    pomo.sessionStartTs = null;
    saveState(state);
    // 超时提示
    if (pomo.plannedMinutes && elapsedMin > pomo.plannedMinutes) {
      setTimeout(function () {
        document.getElementById('modalContent').innerHTML =
          '<h3>🎉 专注完成</h3>' +
          '<p>本次专注 <b>' + elapsedMin + '</b> 分钟，已超过该任务计划的 <b>' + pomo.plannedMinutes + '</b> 分钟。要继续下一项吗？</p>' +
          '<div class="modal-actions"><button class="btn btn-primary" onclick="closeModal()">好的</button></div>';
        openModal();
      }, 100);
    }
    // 自动进入休息倒计时
    if (pomoPref.breakMin > 0) {
      pomo.mode = 'break';
      pomo.remaining = pomoPref.breakMin * 60;
      startCountdown();
      setTimeout(function () {
        document.getElementById('modalContent').innerHTML =
          '<h3>☕ 休息一下</h3><p>专注完成，开始休息 ' + pomoPref.breakMin + ' 分钟。</p>' +
          '<div class="modal-actions"><button class="btn btn-primary" onclick="closeModal()">好的</button></div>';
        openModal();
      }, 100);
    } else {
      pomo.mode = 'focus';
      pomo.remaining = pomoPref.focusMin * 60;
      clearPomoTaskBinding();
      pauseCountdown();
    }
  } else {
    // 休息结束
    pomo.mode = 'focus';
    pomo.remaining = pomoPref.focusMin * 60;
    clearPomoTaskBinding();
    pauseCountdown();
    setTimeout(function () {
      document.getElementById('modalContent').innerHTML =
        '<h3>🔋 休息结束</h3><p>休息结束，继续下一项任务吧～</p>' +
        '<div class="modal-actions"><button class="btn btn-primary" onclick="closeModal()">继续</button></div>';
      openModal();
    }, 100);
  }
  renderPomo();
  renderHistory(loadState());
  // 刷新专注页概览与记录饼图（无论当前停留在哪个页签，元素都已在 DOM 中）
  const fs = loadState();
  renderFocusOverview(fs);
  renderFocusLog(fs);
}

/* 清除任务绑定并同步清空"本次专注事项"输入框 */
function clearPomoTaskBinding() {
  pomo.taskId = null;
  pomo.taskName = '';
  pomo.plannedMinutes = 0;
  const inp = document.getElementById('pomoTaskName');
  if (inp) inp.value = '';
}

/* 从时间轴任务启动专注 */
function startFocusForTask(blockId) {
  const state = loadState();
  ensureDay(state, todayStr());
  ensureTimeline(state, todayStr());
  const b = findTimelineBlock(state, todayStr(), blockId);
  if (!b) { alert('找不到该任务'); return; }
  ensureAudio();
  pomo.taskId = blockId;
  pomo.taskName = b.name;
  // 同步回填到专注页输入框，用户可在此基础上修改
  const inp = document.getElementById('pomoTaskName');
  if (inp) inp.value = b.name;
  // 计划时长：从 start/end 推算，否则用 0
  let planned = 0;
  if (b.start && b.end) {
    const [sh, sm] = b.start.split(':').map(Number);
    const [eh, em] = b.end.split(':').map(Number);
    planned = (eh * 60 + em) - (sh * 60 + sm);
  }
  pomo.plannedMinutes = planned > 0 ? planned : 0;
  pomo.mode = 'focus';
  pomo.remaining = pomoPref.focusMin * 60;
  startCountdown();
  renderPomo();
  // 跳转到「专注」页面查看计时
  switchTab('focus');
}

/* ---------- 专注工作台（独立页） ---------- */
function fmtClock(ts) {
  const d = new Date(ts);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}
/* 专注记录：时间范围（今天/近一周/近一月）+ 按事项聚合饼图 */
let focusRange = 'today';           // today | week | month
let focusPieChart = null;           // Chart.js 实例，切换范围时销毁重建
const FOCUS_COLORS = ['#e0705a', '#5b7fd0', '#4c9f6b', '#2f8fa8', '#8e6fd0', '#d0607a', '#e8a33d', '#6aa66b', '#c4568c', '#5a8fb8'];

function renderFocusPage(state) {
  renderFocusOverview(state);
  renderPomoPref();
  renderPomo();
  renderFocusLog(state);
}
/* 按时间范围从 localStorage 的 state.days[*].focusLog 过滤条目（按 ts 毫秒） */
function collectFocusEntries(state, range) {
  const days = state.days || {};
  if (range === 'today') {
    const day = days[todayStr()];
    return (((day && day.focusLog) || []).slice()).sort(function (a, b) { return (a.ts || 0) - (b.ts || 0); });
  }
  const span = range === 'week' ? 7 : 30; // 近一周=含今天7天，近一月=含今天30天
  const d0 = new Date();
  d0.setHours(0, 0, 0, 0);
  const fromTs = d0.getTime() - (span - 1) * 86400000;
  const all = [];
  Object.keys(days).forEach(function (k) {
    const log = days[k] && days[k].focusLog;
    if (!log) return;
    log.forEach(function (x) { if ((x.ts || 0) >= fromTs) all.push(x); });
  });
  return all.sort(function (a, b) { return (a.ts || 0) - (b.ts || 0); });
}
/* 同名事项合并；按时长降序，最多保留 9 项，其余并入"其他" */
function aggregateFocus(entries) {
  const map = {};
  entries.forEach(function (x) {
    const name = x.name || '自由专注';
    if (!map[name]) map[name] = { name: name, minutes: 0, count: 0 };
    map[name].minutes += (x.minutes || 0);
    map[name].count += 1;
  });
  const arr = Object.keys(map).map(function (k) { return map[k]; })
    .filter(function (x) { return x.minutes > 0; })
    .sort(function (a, b) { return b.minutes - a.minutes; });
  if (arr.length <= 9) return arr;
  const top = arr.slice(0, 9);
  const rest = arr.slice(9);
  top.push({
    name: '其他',
    minutes: rest.reduce(function (s, x) { return s + x.minutes; }, 0),
    count: rest.reduce(function (s, x) { return s + x.count; }, 0),
  });
  return top;
}
/* 自定义图例：色块 + 事项名 + 累计时长 + 占比（Chart 与 CSS 降级共用） */
function focusLegendHtml(agg, total) {
  return '<div class="focus-legend">' + agg.map(function (x, i) {
    const pct = total > 0 ? Math.round(x.minutes * 100 / total) : 0;
    const color = FOCUS_COLORS[i % FOCUS_COLORS.length];
    return '<div class="flegend-item">' +
      '<span class="flegend-dot" style="background:' + color + '"></span>' +
      '<span class="flegend-name">' + escapeHtml(x.name) + '（' + x.count + ' 次）</span>' +
      '<span class="flegend-min">' + fmtMinutes(x.minutes) + '</span>' +
      '<span class="flegend-pct">' + pct + '%</span>' +
    '</div>';
  }).join('') + '</div>';
}
/* 饼图：优先 Chart.js；CDN 失败时用 CSS 色块条降级 */
function renderFocusPie(entries) {
  const canvas = document.getElementById('focusPie');
  const fallback = document.getElementById('focusPieFallback');
  const legend = document.getElementById('focusLegend');
  if (!canvas || !fallback) return;
  const agg = aggregateFocus(entries);
  const total = agg.reduce(function (s, x) { return s + x.minutes; }, 0);
  if (legend) legend.innerHTML = total > 0 ? focusLegendHtml(agg, total) : '';

  if (!agg.length || total <= 0) {
    // 注意：先 destroy（Chart 会还原 canvas 行内样式），再隐藏 canvas
    if (focusPieChart) { focusPieChart.destroy(); focusPieChart = null; }
    canvas.style.display = 'none';
    fallback.hidden = false;
    fallback.innerHTML = '<p class="empty">该时段暂无专注记录</p>';
    return;
  }

  if (window.Chart) {
    if (focusPieChart) { focusPieChart.destroy(); focusPieChart = null; }
    fallback.hidden = true;
    fallback.innerHTML = '';
    canvas.style.display = '';
    focusPieChart = new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: agg.map(function (x) { return x.name; }),
        datasets: [{
          data: agg.map(function (x) { return x.minutes; }),
          backgroundColor: agg.map(function (x, i) { return FOCUS_COLORS[i % FOCUS_COLORS.length]; }),
          borderColor: '#ffffff',
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '40%',
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (item) {
                const x = agg[item.dataIndex];
                const pct = Math.round(x.minutes * 100 / total);
                return x.name + '：' + fmtMinutes(x.minutes) + '（' + pct + '%）';
              },
            },
          },
        },
      },
    });
  } else {
    // 降级：按百分比横向拼接色块（先 destroy，Chart 会还原 canvas 行内样式）
    if (focusPieChart) { focusPieChart.destroy(); focusPieChart = null; }
    canvas.style.display = 'none';
    fallback.hidden = false;
    fallback.innerHTML = '<div class="fallback-bar">' + agg.map(function (x, i) {
      const pct = Math.max(2, Math.round(x.minutes * 100 / total));
      return '<div class="fallback-seg" title="' + escapeHtml(x.name) + ' ' + fmtMinutes(x.minutes) + '" style="flex:' + x.minutes + ' 1 0;background:' + FOCUS_COLORS[i % FOCUS_COLORS.length] + '"></div>';
    }).join('') + '</div>';
  }
}
function renderFocusOverview(state) {
  const el = document.getElementById('focusOverview');
  if (!el) return;
  const todayMin = getTodayFocusMinutes(state);
  const pomos = getTodayPomodoros();
  const maxMin = getTodayMaxFocus(state);
  const weekMin = getWeekFocusMinutes(state);
  el.innerHTML =
    '<div class="focus-stat-grid">' +
      '<div class="focus-stat-item"><div class="focus-stat-num">' + fmtMinutes(todayMin) + '</div><div class="focus-stat-label">今日专注总时长</div></div>' +
      '<div class="focus-stat-item"><div class="focus-stat-num">' + pomos + '</div><div class="focus-stat-label">今日完成番茄钟</div></div>' +
      '<div class="focus-stat-item"><div class="focus-stat-num">' + fmtMinutes(maxMin) + '</div><div class="focus-stat-label">今日最长单次</div></div>' +
      '<div class="focus-stat-item"><div class="focus-stat-num">' + fmtMinutes(weekMin) + '</div><div class="focus-stat-label">本周累计专注</div></div>' +
    '</div>';
}
function renderFocusLog(state) {
  const el = document.getElementById('focusLogList');
  if (!el) return;
  // 同步范围切换按钮高亮
  document.querySelectorAll('#focusRangeTabs .focus-range-tab').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.range === focusRange);
  });
  const rangeLabel = focusRange === 'today' ? '今天' : (focusRange === 'week' ? '近一周' : '近一月');
  const entries = collectFocusEntries(state, focusRange);
  const total = entries.reduce(function (s, x) { return s + (x.minutes || 0); }, 0);

  let html = '<div class="focuslog-range-total">' + rangeLabel + '共 <b>' + entries.length + '</b> 次专注 · 合计 <b>' + fmtMinutes(total) + '</b>，已自动计入各任务的有效学习时长</div>';
  if (!entries.length) {
    html += '<p class="empty">该时段暂无专注记录 · 在上方填写"本次专注事项"后开始计时吧</p>';
    el.innerHTML = html;
  } else {
    // 最近的记录排最前
    const list = entries.slice().reverse();
    html += '<div class="focuslog-list">' + list.map(function (x) {
      const tag = (x.subject && SUBJECT_LABELS[x.subject]) ? '<span class="focuslog-subj">' + SUBJECT_LABELS[x.subject] + '</span>' : '';
      // 周/月范围下在时间列前补充日期
      const datePrefix = focusRange === 'today' ? '' : (toStr(new Date(x.ts)).slice(5) + '<br>');
      return '<div class="focuslog-item">' +
        '<div class="focuslog-time">' + datePrefix + x.start + '<br>- ' + x.end + '</div>' +
        '<div class="focuslog-body">' +
          '<div class="focuslog-name">' + tag + escapeHtml(x.name) + '</div>' +
          '<div class="focuslog-dur">🍅 专注 ' + x.minutes + ' 分钟</div>' +
        '</div>' +
      '</div>';
    }).join('') + '</div>';
    el.innerHTML = html;
  }
  // 隐藏面板中初始化 canvas 会尺寸错误，延迟到 display 生效后绘制
  setTimeout(function () { renderFocusPie(entries); }, 50);
}

/* 防误触：专注中关闭页面提醒 */
window.addEventListener('beforeunload', function (e) {
  if (pomo.running && pomo.mode === 'focus') {
    e.preventDefault();
    e.returnValue = '你正在专注中，确定要离开吗？';
    return e.returnValue;
  }
});

let audioCtx = null;
function ensureAudio() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
  } catch (e) {}
}
function beep() {
  try {
    if (!audioCtx) return;
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.connect(g); g.connect(audioCtx.destination);
    o.type = 'sine'; o.frequency.value = 880;
    g.gain.value = 0.15;
    o.start(); o.stop(audioCtx.currentTime + 0.4);
  } catch (e) {}
}

/* ---------- 屏幕使用时间提醒 ---------- */
let studySeconds = 0;
let studyMinuteAccum = 0;

function showRestReminder() {
  document.getElementById('modalContent').innerHTML =
    '<h3>🕐 该休息啦</h3>' +
    '<p class="remind-text">你已经连续学习 ' + STUDY_REMIND_MIN + ' 分钟，起来活动一下，休息 ' + REST_MIN + ' 分钟吧～</p>' +
    '<div class="modal-actions"><button class="btn btn-primary" onclick="closeModal()">好的，休息一下</button></div>';
  openModal();
}

function studyTick() {
  if (document.hidden) return;
  studySeconds++;
  if (studySeconds >= STUDY_REMIND_MIN * 60) {
    studySeconds = 0;
    showRestReminder();
  }
  studyMinuteAccum++;
  if (studyMinuteAccum >= 60) {
    studyMinuteAccum -= 60;
    const state = loadState();
    const day = ensureDay(state, todayStr());
    day.studyMinutes = (day.studyMinutes || 0) + 1;
    saveState(state);
  }
}
function initStudyTracker() { setInterval(studyTick, 1000); }

/* ---------- 标签页 ---------- */
function switchTab(tab) {
  document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });
  document.querySelectorAll('.tabbar .tab').forEach(function (t) { t.classList.remove('active'); });
  document.getElementById('panel-' + tab).classList.add('active');
  document.querySelector('.tabbar .tab[data-tab="' + tab + '"]').classList.add('active');
  renderTab(tab);
  window.scrollTo(0, 0);
}

function renderTab(tab) {
  const state = loadState();
  const today = todayStr();
  if (tab === 'today') renderToday(state);
  else if (tab === 'tasks') renderTasksPage(state, today);
  else if (tab === 'schedule') {
    renderWeekdayTabs();
    renderDayCourses();
    renderScheduleGrid(state);
    renderFixedSchedule();
  } else if (tab === 'mistakes') renderErrorBook(state);
  else if (tab === 'reflection') renderReflection(state, today);
  else if (tab === 'stats') renderStats(state);
  else if (tab === 'focus') renderFocusPage(state);
  else if (tab === 'me') renderMe(state);
}

function renderDesignAlert(dateStr) {
  const week = getWeekNumber(dateStr);
  const el = document.getElementById('designAlert');
  if (!el) return;
  if (week === DESIGN_ALERT_WEEK) {
    el.hidden = false;
    el.textContent = '⚠️ 机器学习课程设计已进入最终冲刺阶段，请暂停部分考研任务，每天至少安排 1 小时全力完成课设！';
  } else {
    el.hidden = true;
  }
}

function renderToday(state) {
  const today = todayStr();
  timelineViewDate = today;            // 每次进入"今日"页，查看日期回归今天
  ensureDay(state, today);
  renderDesignAlert(today);
  renderWordMini(state, today);
  renderTimeline(state, today);        // 时间轴 + 六级预警 + 错题本
  renderPomoPref();
  renderPomo();
  // 分类任务清单、今日反思 已拆分到独立工作台（tasks / reflection），由 renderTab 路由时渲染
}
/* 打卡/评分后统一刷新视图（时间轴可能正停留在历史日期；分类清单始终操作今天） */
function refreshTodayViews() {
  const state = loadState();
  renderTimeline(state, timelineViewDate);
  renderTasksPage(state, todayStr());  // 新工作台 2：分类任务清单
  renderWordMini(state, todayStr());
  renderWordBlock(state, todayStr());
  renderProblemBlock(state, todayStr());
  renderPomo();
  renderProcrastinationStats(state);
}
function renderWordStatsCard(state) {
  const el = document.getElementById('wordStatsCard');
  if (el) el.innerHTML = wordStatsHtml(state);
}
function renderStats(state) {
  renderProgress(state);
  renderWordProgress(state);
  renderWordStatsCard(state);
  renderChart(state);
  renderProcrastinationStats(state);
  renderStudyTime();
  renderPomoStats(state);
  renderHistory(state);
}

/* ---------- 统计页：番茄钟累计统计 ---------- */
function renderPomoStats(state) {
  const el = document.getElementById('pomoStats');
  if (!el) return;
  const days = state.days || {};
  let totalPomos = 0, totalMinutes = 0, activeDays = 0;
  Object.keys(days).forEach(function (k) {
    const d = days[k];
    if (!d) return;
    totalPomos += d.pomodoros || 0;
    let min = (d.studyMinutes || 0);
    if (d.timeline) d.timeline.forEach(function (b) { min += (b.actualMinutes || 0); });
    if (d.pendingPool) d.pendingPool.forEach(function (b) { min += (b.actualMinutes || 0); });
    totalMinutes += min;
    if ((d.pomodoros || 0) > 0 || min > 0) activeDays++;
  });
  const todayPomos = getTodayPomodoros();
  const todayMin = getTodayFocusMinutes(state);
  el.innerHTML =
    '<div class="pomo-stat-grid">' +
      '<div class="pomo-stat-item"><div class="pomo-stat-num">' + totalPomos + '</div><div class="pomo-stat-label">累计番茄钟</div></div>' +
      '<div class="pomo-stat-item"><div class="pomo-stat-num">' + fmtMinutes(totalMinutes) + '</div><div class="pomo-stat-label">累计专注时长</div></div>' +
      '<div class="pomo-stat-item"><div class="pomo-stat-num">' + activeDays + '</div><div class="pomo-stat-label">活跃天数</div></div>' +
    '</div>' +
    '<div class="pomo-stat-today">今日：<b>' + todayPomos + '</b> 个番茄钟 · 专注 <b>' + fmtMinutes(todayMin) + '</b></div>';
}

/* ---------- 统计页：标签页切换 ---------- */
function switchStatsTab(name) {
  document.querySelectorAll('.stats-tab').forEach(function (t) { t.classList.toggle('active', t.dataset.statstab === name); });
  document.querySelectorAll('.stats-tab-panel').forEach(function (p) { p.classList.toggle('active', p.dataset.statstabPanel === name); });
  // 图表在隐藏容器中渲染会尺寸错误，切换到刷题量时重绘
  if (name === 'problems') {
    setTimeout(function () { renderChart(loadState()); }, 50);
  }
}
function renderMe(state) {
  renderSettings(state);
  renderWordGoal(state);
}

/* ---------- 事件绑定 ---------- */
function bindEvents() {
  // 底部导航
  document.querySelectorAll('.tabbar .tab').forEach(function (btn) {
    btn.addEventListener('click', function () { switchTab(btn.dataset.tab); });
  });

  // 学科切换栏（分类任务清单页）—— document 级委托
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('.subject-tab');
    if (!btn || !btn.dataset.subj) return;
    currentSubjectFilter = btn.dataset.subj;
    const state = loadState();
    renderTasksPage(state, todayStr());
  });

  // 统计页标签页切换
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('.stats-tab');
    if (!btn || !btn.dataset.statstab) return;
    switchStatsTab(btn.dataset.statstab);
  });

  // 任务勾选
  document.getElementById('taskList').addEventListener('change', function (e) {
    if (!e.target.matches('input[type="checkbox"]')) return;
    const state = loadState();
    const day = ensureDay(state, todayStr());
    const t = day.tasks.find(function (x) { return x.id === e.target.dataset.id; });
    if (t) t.done = e.target.checked;
    saveState(state);
    renderTasks(state, todayStr());
    renderHistory(state);
  });

  // 进度下拉
  document.getElementById('mathChapter').addEventListener('change', function (e) {
    const state = loadState();
    state.progress = state.progress || defaultProgress();
    state.progress.math.current = parseInt(e.target.value, 10);
    saveState(state);
    renderProgress(state);
  });
  document.getElementById('majorChapter').addEventListener('change', function (e) {
    const state = loadState();
    state.progress = state.progress || defaultProgress();
    state.progress.major.current = parseInt(e.target.value, 10);
    saveState(state);
    renderProgress(state);
  });
  document.getElementById('majorTotal').addEventListener('change', function (e) {
    const state = loadState();
    state.progress = state.progress || defaultProgress();
    state.progress.major.total = parseInt(e.target.value, 10);
    if (state.progress.major.current > state.progress.major.total) state.progress.major.current = state.progress.major.total;
    saveState(state);
    renderProgress(state);
  });

  // 单词 / 刷题输入（输入框在分类清单里动态渲染，用 document 委托）
  const numMap = {
    wordNew: ['words', 'newCount'], wordReview: ['words', 'reviewCount'],
    probMath: ['problems', 'math'], probCs: ['problems', 'cs'],
  };
  document.addEventListener('input', function (e) {
    const cfg = numMap[e.target.id];
    if (!cfg) return;
    const v = Math.max(0, parseInt(e.target.value, 10) || 0);
    const state = loadState();
    const day = ensureDay(state, todayStr());
    day[cfg[0]][cfg[1]] = v;
    saveState(state);
    if (cfg[0] === 'words') {
      updateWordHint(state, todayStr());
      renderWordMini(state, todayStr());
    }
    updateTaskSummary(state, todayStr());
  });

  // 反思
  const reflectMap = {
    reflectAccomplishment: 'accomplishment',
    reflectConfusion: 'confusion',
    reflectTomorrow: 'tomorrow',
  };
  Object.keys(reflectMap).forEach(function (id) {
    document.getElementById(id).addEventListener('input', function (e) {
      const state = loadState();
      const day = ensureDay(state, todayStr());
      day.reflection[reflectMap[id]] = e.target.value;
      saveState(state);
    });
  });

  // 番茄钟：开始/暂停（开始前读取"本次专注事项"输入框）
  document.getElementById('pomoStart').addEventListener('click', function () {
    ensureAudio();
    if (!pomo.running) {
      const nameInput = document.getElementById('pomoTaskName');
      const typed = nameInput ? nameInput.value.trim() : '';
      // 输入了名称就采用输入框；留空则沿用任务卡带入的名称（最终兜底为"自由专注"）
      if (typed) pomo.taskName = typed;
    }
    if (pomo.running) pauseCountdown(); else startCountdown();
    renderPomo();
  });
  // 输入事项名时实时同步到番茄钟状态
  document.getElementById('pomoTaskName').addEventListener('input', function (e) {
    const v = e.target.value.trim();
    if (v) pomo.taskName = v;
  });

  // 专注记录范围切换：今天 / 近一周 / 近一月
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('#focusRangeTabs .focus-range-tab');
    if (!btn || !btn.dataset.range) return;
    focusRange = btn.dataset.range;
    renderFocusLog(loadState());
  });

  // 历史筛选
  document.getElementById('historyFilter').addEventListener('click', function () {
    historyFullOnly = !historyFullOnly;
    const btn = document.getElementById('historyFilter');
    btn.textContent = historyFullOnly ? '查看全部' : '只看全勤';
    btn.classList.toggle('active', historyFullOnly);
    renderHistory(loadState());
  });

  // 课表周切换
  document.getElementById('weekPrev').addEventListener('click', function () {
    scheduleViewWeek = clampScheduleWeek(scheduleViewWeek - 1);
    renderDayCourses();
    renderScheduleGrid(loadState());
  });
  document.getElementById('weekNext').addEventListener('click', function () {
    scheduleViewWeek = clampScheduleWeek(scheduleViewWeek + 1);
    renderDayCourses();
    renderScheduleGrid(loadState());
  });
  document.getElementById('weekToday').addEventListener('click', function () {
    scheduleViewWeek = clampScheduleWeek(getWeekNumber(todayStr()));
    renderDayCourses();
    renderScheduleGrid(loadState());
  });

  // 星期切换栏
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('.weekday-tab');
    if (!btn || !btn.dataset.wd) return;
    selectedWeekday = parseInt(btn.dataset.wd, 10);
    renderWeekdayTabs();
    renderDayCourses();
  });

  // 我的固定课表：周一~周五按钮切换，每次只显示一天
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('.fs-tab');
    if (!btn || !btn.dataset.fswd) return;
    selectedFixedWeekday = parseInt(btn.dataset.fswd, 10);
    renderFixedSchedule();
  });

  // 打卡/评分/时长/专注：document 级委托，时间轴(#timeline，操作查看日期)与任务清单(#tasksList，操作今天)共用
  document.addEventListener('change', function (e) {
    const cb = e.target.closest('input[data-tldone]');
    if (!cb) return;
    const date = eventViewDate(e);
    const state = loadState();
    ensureDay(state, date);
    ensureTimeline(state, date);
    const found = findTaskRef(state, date, cb.dataset.tldone);
    if (found.block && found.sub) {
      found.sub.done = cb.checked;
      found.block.done = found.block.subtasks.every(function (s) { return s.done; });
    } else if (found.block) {
      found.block.done = cb.checked;
      if (found.block.subtasks && found.block.subtasks.length) {
        found.block.subtasks.forEach(function (s) { s.done = cb.checked; });
      }
    }
    saveState(state);
    refreshTodayViews();
  });
  document.addEventListener('input', function (e) {
    const input = e.target.closest('input[data-tlmin]');
    if (!input) return;
    const date = eventViewDate(e);
    const state = loadState();
    ensureDay(state, date);
    ensureTimeline(state, date);
    const found = findTaskRef(state, date, input.dataset.tlmin);
    if (!found.block) return;
    const v = Math.max(0, parseInt(input.value, 10) || 0);
    if (found.sub) {
      found.sub.actualMinutes = v;
      found.block.actualMinutes = found.block.subtasks.reduce(function (a, s) { return a + (s.actualMinutes || 0); }, 0);
    } else {
      found.block.actualMinutes = v;
    }
    saveState(state);
  });
  document.addEventListener('click', function (e) {
    // 星级评分
    const star = e.target.closest('.star[data-tlrate]');
    if (star) {
      const date = eventViewDate(e);
      const state = loadState();
      ensureDay(state, date);
      ensureTimeline(state, date);
      const b = findTimelineBlock(state, date, star.dataset.tlrate);
      if (b) {
        const val = parseInt(star.dataset.val, 10);
        b.rating = (b.rating === val) ? 0 : val; // 再次点击同一星可取消
        saveState(state);
        refreshTodayViews();
      }
      return;
    }
    // 开始专注（非当天的按钮已禁用，只有今天能启动）
    const focusBtn = e.target.closest('[data-tlfocus]');
    if (focusBtn) { startFocusForTask(focusBtn.dataset.tlfocus); return; }
    // 错题复习入口（时间轴上的每日复习块）
    if (e.target.closest('[data-tlfocus-review]')) { openEbReviewMode(loadState()); return; }
    // 编辑按钮
    const editBtn = e.target.closest('[data-tledit]');
    if (editBtn) {
      const date = eventViewDate(e);
      const state = loadState();
      ensureDay(state, date);
      ensureTimeline(state, date);
      const b = findTimelineBlock(state, date, editBtn.dataset.tledit);
      if (b) { editingBlock.id = b.id; openEditModal(b); }
    }
  });

  // 时间轴：前一天 / 后一天 / 跳转日期 / 回到今天
  document.getElementById('tlPrevDay').addEventListener('click', function () {
    setTimelineViewDate(addDays(timelineViewDate, -1));
  });
  document.getElementById('tlNextDay').addEventListener('click', function () {
    setTimelineViewDate(addDays(timelineViewDate, 1));
  });
  document.getElementById('tlBackToday').addEventListener('click', function () {
    setTimelineViewDate(todayStr());
  });
  document.getElementById('tlDatePick').addEventListener('change', function (e) {
    if (e.target.value) setTimelineViewDate(e.target.value);
  });

  // 功能 F：错题本 2.0（加入/照片上传/标记掌握/删除/复习模式/图片预览）
  const ebEl = document.getElementById('errorBook');
  let pendingPhoto = ''; // 暂存待加入的照片 base64
  function compressPhoto(file, cb) {
    const reader = new FileReader();
    reader.onload = function () {
      const img = new Image();
      img.onload = function () {
        const canvas = document.createElement('canvas');
        const maxW = 800, maxH = 800;
        let w = img.width, h = img.height;
        if (w > maxW) { h = h * maxW / w; w = maxW; }
        if (h > maxH) { w = w * maxH / h; h = maxH; }
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, w, h);
        cb(canvas.toDataURL('image/jpeg', 0.7)); // 压缩到 70% 质量
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  }
  ebEl.addEventListener('change', function (e) {
    if (e.target.id === 'ebPhoto') {
      const file = e.target.files[0];
      if (!file) return;
      compressPhoto(file, function (dataUrl) {
        pendingPhoto = dataUrl;
        const prev = document.getElementById('ebPreview');
        prev.hidden = false;
        prev.innerHTML = '<img src="' + dataUrl + '" alt="预览"><button class="eb-photo-clear" id="ebPhotoClear">✕</button>';
      });
      e.target.value = '';
    }
  });
  ebEl.addEventListener('click', function (e) {
    const addBtn = e.target.closest('#ebAddBtn');
    if (addBtn) {
      const input = document.getElementById('ebInput');
      const text = input.value.trim();
      if (!text && !pendingPhoto) { alert('请输入错题文字或上传照片'); return; }
      const state = loadState();
      getErrorBook(state).push({
        id: 'eb-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
        text: text, photo: pendingPhoto, date: todayStr(),
        mastered: false, reviewCount: 0, lastReviewDate: '',
      });
      saveState(state);
      pendingPhoto = '';
      input.value = '';
      renderErrorBook(state);
      renderTimeline(state, todayStr());
      return;
    }
    if (e.target.closest('#ebPhotoClear')) {
      pendingPhoto = '';
      const prev = document.getElementById('ebPreview');
      prev.hidden = true;
      prev.innerHTML = '';
      return;
    }
    // 复习模式入口
    if (e.target.closest('#ebReviewBtn')) {
      openEbReviewMode(loadState());
      return;
    }
    // 图片预览（点击缩略图放大）
    const thumb = e.target.closest('[data-ebphoto]');
    if (thumb) {
      const i = parseInt(thumb.dataset.ebphoto, 10);
      const state = loadState();
      const eb = getErrorBook(state);
      if (eb[i] && eb[i].photo) {
        document.getElementById('modalContent').innerHTML =
          '<img class="eb-photo-full" src="' + eb[i].photo + '" alt="错题原图">' +
          '<div class="modal-actions"><button class="btn btn-primary" onclick="closeModal()">关闭</button></div>';
        openModal();
      }
      return;
    }
    const toggleBtn = e.target.closest('[data-ebtoggle]');
    if (toggleBtn) {
      const idx = parseInt(toggleBtn.dataset.ebtoggle, 10);
      const state = loadState();
      const eb = getErrorBook(state);
      if (eb[idx]) {
        eb[idx].mastered = !eb[idx].mastered;
        if (eb[idx].mastered) { eb[idx].lastReviewDate = todayStr(); }
        saveState(state);
        renderErrorBook(state);
        renderTimeline(state, todayStr());
      }
      return;
    }
    const delBtn = e.target.closest('[data-ebdel]');
    if (delBtn) {
      const idx = parseInt(delBtn.dataset.ebdel, 10);
      const state = loadState();
      const eb = getErrorBook(state);
      eb.splice(idx, 1);
      saveState(state);
      renderErrorBook(state);
      renderTimeline(state, todayStr());
      return;
    }
  });
  ebEl.addEventListener('keydown', function (e) {
    if (e.target.id === 'ebInput' && e.key === 'Enter') {
      document.getElementById('ebAddBtn').click();
    }
  });

  // 错题本复习模式：上一题/下一题/掌握/没掌握/关闭
  const ebReviewEl = document.getElementById('ebReviewOverlay');
  ebReviewEl.addEventListener('click', function (e) {
    if (e.target === ebReviewEl) { closeEbReviewMode(); return; }
    const nav = e.target.closest('[data-ebnav]');
    if (nav) {
      if (nav.dataset.ebnav === 'prev') ebReviewIdx--;
      else if (nav.dataset.ebnav === 'next') ebReviewIdx++;
      renderEbReviewCard();
      return;
    }
    const master = e.target.closest('[data-ebmaster]');
    if (master) {
      const e = ebReviewQueue[ebReviewIdx];
      if (!e) return;
      const state = loadState();
      const eb = getErrorBook(state);
      const item = eb.find(function (x) { return x.id === e.id; });
      if (item) {
        item.lastReviewDate = todayStr();
        if (master.dataset.ebmaster === 'yes') {
          item.mastered = true;
        } else {
          item.reviewCount = (item.reviewCount || 0) + 1;
        }
        saveState(state);
        // 同步队列里这条
        e.mastered = item.mastered;
        e.reviewCount = item.reviewCount;
      }
      // 自动跳到下一题
      if (ebReviewIdx < ebReviewQueue.length - 1) ebReviewIdx++;
      else { closeEbReviewMode(); renderErrorBook(loadState()); renderTimeline(loadState(), todayStr()); return; }
      renderEbReviewCard();
      return;
    }
    if (e.target.closest('#ebReviewClose')) closeEbReviewMode();
  });

  // 番茄钟：自定义专注/休息时长（document 委托，今日卡片与设置页两个挂载点都生效）
  document.addEventListener('click', function (e) {
    const f = e.target.closest('[data-pomofocus]');
    if (f) {
      pomoPref.focusMin = parseInt(f.dataset.pomofocus, 10);
      savePomoPref(pomoPref);
      if (!pomo.running) { pomo.remaining = pomoPref.focusMin * 60; }
      renderPomoPref(); renderPomo();
      return;
    }
    const b = e.target.closest('[data-pomobreak]');
    if (b) {
      pomoPref.breakMin = parseInt(b.dataset.pomobreak, 10);
      savePomoPref(pomoPref);
      renderPomoPref(); renderPomo();
      return;
    }
  });
  document.addEventListener('input', function (e) {
    if (e.target.classList && e.target.classList.contains('pomo-custom')) {
      const v = parseInt(e.target.value, 10);
      if (v && v > 0 && v <= 180) {
        pomoPref.focusMin = v;
        savePomoPref(pomoPref);
        if (!pomo.running) { pomo.remaining = v * 60; }
        renderPomo();
      }
    }
  });

  // 番茄钟：重置时使用当前 pref，并清空事项输入框
  document.getElementById('pomoReset').addEventListener('click', function () {
    pauseCountdown();
    pomo.mode = 'focus';
    pomo.remaining = pomoPref.focusMin * 60;
    clearPomoTaskBinding();
    renderPomo();
  });

  // 单词设置保存（我的页：每日新词 / 每日复习 / 大纲总词量）
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('.js-wsave-btn');
    if (!btn) return;
    const wrap = btn.closest('.word-settings') || document;
    const dailyNew = parseInt(wrap.querySelector('.js-ws-new').value, 10);
    const dailyReview = parseInt(wrap.querySelector('.js-ws-review').value, 10);
    const target = parseInt(wrap.querySelector('.js-ws-target').value, 10);
    if (!dailyNew || dailyNew < 1 || dailyNew > 500) { alert('每日新词数请输入 1-500 之间的数字'); return; }
    if (isNaN(dailyReview) || dailyReview < 0 || dailyReview > 2000) { alert('每日复习数请输入 0-2000 之间的数字'); return; }
    if (!target || target < 500 || target > 20000) { alert('大纲总词量请输入 500-20000 之间的数字'); return; }
    const state = loadState();
    state.wordSettings = { dailyNew: dailyNew, dailyReview: dailyReview, target: target };
    state.wordGoal = target; // 兼容旧字段
    saveState(state);
    renderWordGoal(state);
    renderWordStatsCard(state);
    renderWordProgress(state);
    renderWordMini(state, todayStr());
    updateWordHint(state, todayStr());
    alert('单词设置已保存');
  });

  // 设置：考研 / 六级日期保存
  document.getElementById('settingsDateBtn').addEventListener('click', function () {
    const state = loadState();
    const exam = document.getElementById('setExamDate').value;
    const cet6 = document.getElementById('setCet6Date').value;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(exam) || !/^\d{4}-\d{2}-\d{2}$/.test(cet6)) {
      alert('日期格式应为 YYYY-MM-DD'); return;
    }
    state.examDate = exam;
    state.cet6Date = cet6;
    saveState(state);
    renderHeader();
    renderTimeline(state, todayStr());
    renderWordGoal(state);
    renderWordStatsCard(state);
    renderWordMini(state, todayStr());
    alert('已保存：考研 ' + exam + '，六级 ' + cet6);
  });

  // 战报
  document.getElementById('reportBtn').addEventListener('click', generateReport);

  // 数据导出 / 重置
  document.getElementById('exportBtn').addEventListener('click', function () {
    const blob = new Blob([JSON.stringify(loadState(), null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'study-checkin-' + todayStr() + '.json';
    a.click();
    URL.revokeObjectURL(url);
  });
  // 功能 E：导出备份（含 DAILY_PLAN_DATA + 打卡记录 + 错题本）
  document.getElementById('backupBtn').addEventListener('click', function () {
    const state = loadState();
    const backup = {
      version: '2',
      exportDate: todayStr(),
      dailyPlanData: typeof DAILY_PLAN_DATA !== 'undefined' ? DAILY_PLAN_DATA : [],
      state: state,
    };
    const blob = new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'kaoyan-backup-' + todayStr() + '.json';
    a.click();
    URL.revokeObjectURL(url);
  });
  // 功能 E：导入备份（恢复全部数据）
  document.getElementById('importFile').addEventListener('change', function (e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function () {
      try {
        const backup = JSON.parse(reader.result);
        if (!backup || !backup.state) { alert('文件格式不正确：缺少 state 字段'); return; }
        if (!confirm('导入备份会覆盖当前所有数据，确定继续吗？')) return;
        const state = migrateState(backup.state);
        saveState(state);
        alert('备份导入成功，即将刷新页面');
        location.reload();
      } catch (err) {
        alert('导入失败：' + err.message);
      }
    };
    reader.readAsText(file);
    e.target.value = ''; // 允许重复导入同一文件
  });
  document.getElementById('resetBtn').addEventListener('click', function () {
    if (confirm('确定清空全部打卡数据吗？此操作不可恢复。')) {
      localStorage.removeItem(STORE_KEY);
      location.reload();
    }
  });

  // 弹窗点背景关闭
  document.getElementById('modalOverlay').addEventListener('click', function (e) {
    if (e.target === this) closeModal();
  });
}

/* ---------- CDN 动态加载（断网降级） ---------- */
function loadScript(src, onload) {
  const s = document.createElement('script');
  s.src = src;
  s.onload = onload || function () {};
  s.onerror = function () { console.warn('CDN 加载失败（可离线降级）：' + src); };
  document.head.appendChild(s);
}

/* ---------- 初始化 ---------- */
let appDate = todayStr();

function initTimelineRefresh() {
  // 每分钟刷新：跨天自动重载；否则更新超时提醒状态（保留用户当前查看日期）
  setInterval(function () {
    if (todayStr() !== appDate) { location.reload(); return; }
    if (document.hidden) return;
    renderTimeline(loadState(), timelineViewDate);
    if (document.getElementById('categoryTasks')) renderTodayChecklist(loadState(), todayStr());
  }, 60000);
}

function init() {
  const state = migrateState(loadState());
  const today = todayStr();
  ensureTimeline(state, today);
  saveState(state);

  renderHeader();
  renderToday(state);
  bindEvents();
  initStudyTracker();
  initTimelineRefresh();

  // 启动时校验时间轴与真实课表是否冲突，结果输出到控制台
  try {
    const conflicts = validateAllTimelines();
    if (conflicts.length) {
      console.warn('⚠️ 检测到 ' + conflicts.length + ' 处「考研任务 × 上课节次」时间冲突，可运行 console.table(validateAllTimelines()) 查看');
    } else {
      console.info('✅ 课表冲突校验通过：时间轴任务与所有上课节次无重叠');
    }
  } catch (e) { /* 校验失败不影响主流程 */ }

  loadScript(CHART_CDN, function () {
    renderChart(loadState());
    // CDN 到达后把专注记录的 CSS 降级色块升级为真正的饼图
    if (document.getElementById('focusLogList')) renderFocusLog(loadState());
  });
  loadScript(HTML2CANVAS_CDN, function () {});
}

init();
