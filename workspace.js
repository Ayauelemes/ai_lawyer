const root = document.querySelector("#view");
const notice = document.querySelector("#notice");
const nav = document.querySelector("#navigation");
const locale = document.querySelector("#locale");
const state = { lang: localStorage.getItem("zh-language") || "ru", token: "", user: null, page: "home", caseId: null };
const pages = {
  home: ["Рабочий стол", "Жұмыс үстелі"],
  cases: ["Дела", "Істер"],
  analysis: ["ИИ-юрист и заключения", "ЖИ заңгері және қорытындылар"],
  evidence: ["Документы", "Құжаттар"],
  norms: ["Законодательство", "Заңнама"],
  practice: ["Судебная практика", "Сот практикасы"],
  penalty: ["Расчёт санкций", "Санкцияларды есептеу"],
  admin: ["Администрирование", "Әкімші панелі"],
  settings: ["Настройки", "Баптаулар"],
};
const statuses = {
  draft: ["Проект", "Жоба"], approved: ["Утверждено", "Бекітілді"],
  returned: ["Возвращено", "Қайтарылды"], withdrawn: ["Отозвано", "Кері қайтарылды"],
  review_required: ["Требуется повторная проверка", "Қайта тексеру қажет"],
  extracted: ["Текст извлечён", "Мәтін алынды"], no_text: ["Текст не извлечён", "Мәтін алынбады"],
  partial: ["Извлечено частично", "Ішінара алынды"],
  conditional_calculation: ["Условный расчёт", "Шартты есептеу"],
};
const errors = {
  authentication_required: ["Введите действующий ключ доступа.", "Жарамды кіру кілтін енгізіңіз."],
  insufficient_role: ["Недостаточно прав.", "Құқықтар жеткіліксіз."],
  llm_not_configured: ["Администратору нужно настроить модель.", "Әкімші модельді баптауы керек."],
  case_transfer_disabled: ["Передача материалов провайдеру отключена.", "Материалдарды провайдерге жіберу өшірілген."],
  invalid_model_response: ["Модель вернула ответ неверного формата.", "Модель жарамсыз форматтағы жауап берді."],
  unverified_citation: ["Проект отклонён: ссылка или цитата не подтверждена.", "Жоба қабылданбады: сілтеме немесе дәйексөз расталмады."],
  provider_unavailable: ["Модель недоступна. Повторите позже.", "Модель қолжетімсіз. Кейін қайталаңыз."],
  no_verified_norm_matches: ["Подходящие проверенные нормы не найдены.", "Сәйкес тексерілген нормалар табылмады."],
  no_verified_judgment_matches: ["Подходящие проверенные судебные акты не найдены.", "Сәйкес тексерілген сот актілері табылмады."],
  evidence_extraction_incomplete: ["Часть вложений требует ручной проверки.", "Кейбір тіркемелерді қолмен тексеру қажет."],
  event_date_required: ["Для проверки редакции укажите дату события.", "Редакцияны тексеру үшін оқиға күнін көрсетіңіз."],
  ocr_unavailable: ["OCR не установлен.", "OCR орнатылмаған."],
  ocr_failed: ["OCR не смог обработать страницу.", "OCR бетті өңдей алмады."],
};
const fields = {
  facts: ["Обстоятельства", "Мән-жайлар"], issues: ["Правовые вопросы", "Құқықтық мәселелер"],
  questions: ["Уточняющие вопросы", "Нақтылау сұрақтары"], applicant: ["Позиция заявителя", "Өтініш берушінің ұстанымы"],
  respondent: ["Возможная позиция другой стороны", "Екінші тараптың ықтимал ұстанымы"],
  evidence: ["Доказательства", "Дәлелдемелер"], legal: ["Правовые основания", "Құқықтық негіздер"],
  practice: ["Аналогичные дела", "Ұқсас істер"], civil: ["Гражданские последствия", "Азаматтық салдар"],
  administrative: ["Административная ответственность", "Әкімшілік жауаптылық"],
  conclusion: ["Предварительный вывод", "Алдын ала қорытынды"], next_actions: ["Дальнейшие действия", "Кейінгі қадамдар"],
};
const tr = (ru, kk) => state.lang === "kk" ? kk : ru;
const t = pair => tr(...pair);
const labelStatus = value => statuses[value] ? t(statuses[value]) : value;
const message = value => errors[value] ? t(errors[value]) : value;

function element(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === "className") node.className = value;
    else if (key === "on") node.addEventListener(value[0], value[1]);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  node.append(...children.flat().filter(child => child !== null && child !== undefined));
  return node;
}
const paragraph = (value, className) => element("p", {className}, value);
const heading = (value, level = 2) => element("h" + level, {}, value);
const group = (...children) => element("div", {className: "row"}, children);
const card = (...children) => element("section", {className: "card"}, children);
const tag = value => element("span", {className: "tag"}, labelStatus(value));
const blank = () => paragraph(tr("Записей пока нет.", "Әзірге жазбалар жоқ."), "muted");

function alertMessage(value, bad = false) {
  notice.textContent = value;
  notice.hidden = !value;
  notice.className = bad ? "error" : "";
}
function action(title, work, primary = false) {
  return element("button", {type: "button", className: primary ? "primary" : "",
    on: ["click", async event => {
      const button = event.currentTarget;
      button.disabled = true;
      alertMessage("");
      try { await work(); }
      catch (error) { alertMessage(error.message, true); }
      finally { button.disabled = false; }
    }]}, title);
}
function formControl(name, title, options = {}) {
  const {type = "text", value = "", choices = [], required = true} = options;
  const input = element(choices.length ? "select" : type === "textarea" ? "textarea" : "input",
    {name, type: choices.length || type === "textarea" ? null : type, required: required ? "" : null,
      accept: options.accept, maxlength: options.maxlength});
  if (choices.length) input.append(...choices.map(([id, text]) => element("option", {value: id}, text)));
  if (type !== "file") input.value = value;
  return element("label", {}, title, input);
}
function form(title, controls, handler) {
  const submit = element("button", {className: "primary", type: "submit"}, title);
  return element("form", {className: "stack", on: ["submit", async event => {
    event.preventDefault();
    submit.disabled = true;
    alertMessage("");
    try { await handler(new FormData(event.currentTarget)); }
    catch (error) { alertMessage(error.message, true); }
    finally { submit.disabled = false; }
  }]}, controls, group(submit));
}
const body = data => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});

async function api(path, options = {}, download = false) {
  const response = await fetch(path, {...options, headers: {...options.headers, Authorization: "Bearer " + state.token}});
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    const detail = problem.detail;
    throw new Error(typeof detail === "string" ? message(detail) : Array.isArray(detail) ?
      detail.map(item => item.loc.join(".") + " · " + item.type).join(" / ") : String(response.status));
  }
  return download ? response.blob() : response.json();
}
async function saveFile(path, options, filename) {
  const blob = await api(path, options, true);
  const url = URL.createObjectURL(blob);
  const link = element("a", {href: url, download: filename});
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}
function external(url, title) {
  if (!url || !url.startsWith("https://")) return paragraph(title);
  return element("a", {href: url, target: "_blank", rel: "noopener noreferrer"}, title + " ↗");
}
const caseRoles = ["operator", "lawyer", "admin"];
const reviewer = () => ["lawyer", "admin"].includes(state.user?.role);
const curator = () => ["curator", "admin"].includes(state.user?.role);
const auditor = () => ["auditor", "admin"].includes(state.user?.role);

function updateNav() {
  nav.replaceChildren();
  for (const [page, title] of Object.entries(pages)) {
    if (!state.user && !["home", "settings"].includes(page)) continue;
    if (["cases", "analysis", "evidence"].includes(page) && !caseRoles.includes(state.user?.role)) continue;
    if (page === "admin" && !curator() && !auditor()) continue;
    if (page === "penalty" && !reviewer()) continue;
    const button = action(t(title), () => show(page));
    if (page === state.page) button.setAttribute("aria-current", "page");
    nav.append(button);
  }
  document.querySelector("#section").textContent = t(pages[state.page]);
  document.querySelector("#account").textContent = state.user ? state.user.name + " · " + state.user.tenant : "";
}
async function show(page) {
  state.page = page;
  updateNav();
  const view = element("div", {}, heading(t(pages[page]), 1));
  root.replaceChildren(view);
  if (!state.user) {
    view.append(card(heading(tr("Вход в систему", "Жүйеге кіру")),
      paragraph(tr("Используйте ключ доступа, выданный администратором.", "Әкімші берген кіру кілтін қолданыңыз.")),
      form(tr("Войти", "Кіру"), [formControl("token", tr("Ключ доступа", "Кіру кілті"), {type: "password"})],
        async data => {
          state.token = data.get("token").trim();
          try { state.user = await api("/v1/me"); }
          catch (error) { state.token = ""; throw error; }
          await show("home");
        })));
    return;
  }
  const screens = {home, cases, analysis: analysisPage, evidence: evidencePage,
    norms: target => sourcesPage(target, "norm"), practice: target => sourcesPage(target, "judgment"),
    penalty: penaltyPage, admin: adminPage, settings: settingsPage};
  await screens[page](view);
}
async function home(view) {
  view.append(card(heading(tr("Рабочее пространство по вопросам ЖКХ", "Тұрғын үй-коммуналдық шаруашылық бойынша жұмыс кеңістігі")),
    paragraph(tr("Ведите дела, изучайте источники и проверяйте проекты заключений.", "Істерді жүргізіңіз, дереккөздерді қараңыз және қорытынды жобаларын тексеріңіз.")),
    group(caseRoles.includes(state.user.role) ? action(tr("Описать ситуацию", "Жағдайды сипаттау"), () => {
        state.caseId = null;
        return show("cases");
      }, true) : null,
      action(t(pages.norms), () => show("norms")), action(t(pages.practice), () => show("practice")))),
    card(heading(tr("Подключение ИИ", "ЖИ қосылымы")), modelStatus()));
  if (caseRoles.includes(state.user.role)) {
    const recent = await api("/v1/cases?limit=5");
    view.append(card(heading(tr("Недавние дела", "Соңғы істер")),
      recent.length ? recent.map(item => group(
        action(item.title, () => { state.caseId = item.id; return show("cases"); }),
        element("small", {}, item.region))) : blank(),
      group(action(t(pages.cases), () => show("cases")))));
  }
}
function modelStatus() {
  if (!state.user.llm_configured) return paragraph(message("llm_not_configured"), "muted");
  return paragraph(state.user.llm_local ? tr("Локальная модель настроена.", "Жергілікті модель бапталған.") :
    tr("Внешняя модель настроена.", "Сыртқы модель бапталған."));
}
function settingsPage(view) {
  view.append(card(heading(tr("Профиль", "Профиль")),
    paragraph(state.user.name + " · " + state.user.role), modelStatus(),
    action(tr("Выйти", "Шығу"), () => {
      Object.assign(state, {token: "", user: null, caseId: null});
      return show("home");
    })));
}
const langChoices = [["ru", "Русский"], ["kk", "Қазақша"]];

function caseForm(row) {
  const data = row || {};
  return form(row ? tr("Сохранить", "Сақтау") : tr("Создать дело", "Іс ашу"), [
    formControl("title", tr("Название дела", "Іс атауы"), {value: data.title, maxlength: 240}),
    formControl("region", tr("Город или регион", "Қала немесе өңір"), {value: data.region, maxlength: 120}),
    formControl("event_on", tr("Дата события", "Оқиға күні"), {value: data.event_on || "", type: "date", required: false}),
    formControl("language", tr("Язык заключения", "Қорытынды тілі"), {value: data.language || state.lang, choices: langChoices}),
    formControl("narrative", tr("Обстоятельства и требования", "Мән-жайлар мен талаптар"), {value: data.narrative, type: "textarea", maxlength: 12000}),
  ], async formData => {
    const entry = Object.fromEntries(formData);
    entry.event_on ||= null;
    const options = body(entry);
    if (row) options.method = "PUT";
    const saved = await api("/v1/cases" + (row ? "/" + row.id : ""), options);
    state.caseId = saved.id;
    await show("cases");
  });
}
async function cases(view) {
  const items = await api("/v1/cases");
  const list = card(heading(tr("История дел", "Істер тарихы")));
  if (!items.length) list.append(blank());
  for (const item of items) {
    list.append(element("div", {className: "list-item"}, heading(item.title, 3),
      paragraph([item.region, item.event_on].filter(Boolean).join(" · "), "muted"),
      action(tr("Открыть", "Ашу"), () => { state.caseId = item.id; return show("cases"); })));
  }
  const selected = state.caseId ? await api("/v1/cases/" + state.caseId) : null;
  view.append(element("div", {className: "grid"}, list, card(
    heading(selected ? selected.title : tr("Новое дело", "Жаңа іс")),
    selected ? group(action(t(pages.analysis), () => show("analysis"), true),
      action(t(pages.evidence), () => show("evidence")),
      action(tr("Новое дело", "Жаңа іс"), () => { state.caseId = null; return show("cases"); })) : null,
    caseForm(selected))));
}
async function activeCase(view) {
  if (!state.caseId) {
    view.append(card(paragraph(tr("Сначала откройте дело.", "Алдымен істі ашыңыз.")), action(t(pages.cases), () => show("cases"))));
    return null;
  }
  const item = await api("/v1/cases/" + state.caseId);
  view.append(card(heading(item.title), group(action(t(pages.cases), () => show("cases")),
    action(t(pages.evidence), () => show("evidence")), action(t(pages.analysis), () => show("analysis")))));
  return item;
}

async function evidencePage(view) {
  const item = await activeCase(view);
  if (!item) return;
  const items = await api("/v1/cases/" + item.id + "/evidence");
  const detail = card(heading(tr("Извлечённый текст", "Алынған мәтін")), blank());
  const list = card(heading(tr("Материалы дела", "Іс материалдары")));
  if (!items.length) list.append(blank());
  for (const entry of items) {
    list.append(element("div", {className: "list-item"}, group(entry.filename, tag(entry.status)),
      group(action(tr("Читать текст", "Мәтінді оқу"), async () => {
        const record = await api("/v1/evidence/" + entry.id);
        detail.replaceChildren(heading(record.filename),
          ...record.warnings.map(warning => paragraph(message(warning), "muted")),
          ...(record.chunks.length ? record.chunks.map(chunk =>
            element("div", {}, heading(chunk.location, 3), element("pre", {}, chunk.text))) : [blank()]));
      }), action(tr("Скачать оригинал", "Түпнұсқаны жүктеу"),
        () => saveFile("/v1/evidence/" + entry.id + "/download", {}, entry.filename)))));
  }
  view.append(card(heading(tr("Загрузка", "Жүктеу")),
    form(tr("Загрузить", "Жүктеу"), [formControl("file", "PDF, DOCX, JPG, PNG", {type: "file", accept: ".pdf,.docx,.jpg,.jpeg,.png"})],
      async data => { await api("/v1/cases/" + item.id + "/evidence", {method: "POST", body: data}); await show("evidence"); })),
    element("div", {className: "grid"}, list, detail));
}

function sourceItem(source) {
  const details = source.details || {};
  const info = source.kind === "norm" ?
    [details.act_id, details.article && tr("ст. ", "бап ") + details.article,
      details.part && tr("ч. ", "бөлік ") + details.part, details.paragraph && tr("п. ", "тармақ ") + details.paragraph,
      details.subparagraph && tr("подп. ", "тармақша ") + details.subparagraph, source.start, source.end].filter(Boolean) :
    [details.court, details.case_number, details.decided_on, details.instance, details.outcome].filter(Boolean);
  return element("div", {className: "list-item"},
    source.url ? external(source.url, source.title) : heading(source.title, 3),
    info.length ? paragraph(info.join(" · "), "muted") : null,
    source.text ? element("blockquote", {}, source.text) : null);
}

async function analysisPage(view) {
  const item = await activeCase(view);
  if (!item) return;
  const history = await api("/v1/cases/" + item.id + "/analyses");
  const detail = element("div");
  const consent = element("input", {type: "checkbox", name: "allow_provider"});
  const controls = state.user.llm_local ? [] : [element("label", {className: "check"}, consent,
    tr("Разрешаю передачу материалов настроенному провайдеру для этого анализа.",
      "Осы талдау үшін материалдарды бапталған провайдерге жіберуге рұқсат беремін."))];
  const generation = state.user.llm_configured ?
    form(tr("Запустить анализ", "Талдауды бастау"), controls, async data => {
      const result = await api("/v1/cases/" + item.id + "/analyses", body({allow_provider: data.has("allow_provider")}));
      await show("analysis");
      alertMessage(tr("Проект сохранён в истории заключений.", "Жоба қорытындылар тарихына сақталды."));
      const target = document.querySelector("#analysis-result");
      if (target) renderOpinion(target, result);
    }) : modelStatus();
  detail.id = "analysis-result";
  view.append(card(heading(tr("Анализ материалов дела", "Іс материалдарын талдау")), generation),
    card(heading(tr("История заключений", "Қорытындылар тарихы")),
      history.length ? history.map(record => group(tag(record.status), record.created_at.slice(0, 10),
        action(tr("Открыть заключение", "Қорытындыны ашу"), async () =>
          renderOpinion(detail, await api("/v1/analyses/" + record.id))))) : blank()), detail);
}
function renderOpinion(view, result) {
  const lookup = new Map(result.sources.map(source => [source.id, source]));
  const box = card(group(heading(result.opinion.category), tag(result.status)),
    ...result.limitations.map(warning => paragraph(message(warning), "muted")));
  for (const [key, title] of Object.entries(fields)) {
    if (!result.opinion[key].length) continue;
    box.append(heading(t(title), 3));
    for (const entry of result.opinion[key]) {
      if (typeof entry === "string") { box.append(paragraph(entry)); continue; }
      const source = lookup.get(entry.source_id);
      box.append(paragraph(entry.text || entry.assessment),
        element("blockquote", {}, entry.quote || entry.excerpt),
        entry.similarities ? paragraph(entry.similarities) : element("span"),
        entry.differences ? paragraph(entry.differences) : element("span"));
      if (source) box.append(external(source.url, source.title));
    }
  }
  if (result.sources.length) {
    const sources = element("details", {}, element("summary", {}, tr("Источники анализа", "Талдау дереккөздері")));
    sources.append(...result.sources.map(sourceItem));
    box.append(sources);
  }
  view.replaceChildren(box);
  if (reviewer() && result.status === "draft") {
    view.append(card(heading(tr("Проверка заключения", "Қорытындыны тексеру")), form(tr("Сохранить решение", "Шешімді сақтау"), [
      formControl("status", tr("Решение", "Шешім"), {value: "returned", choices: [
        ["returned", tr("Вернуть на доработку", "Қайта өңдеуге қайтару")],
        ["approved", tr("Утвердить", "Бекіту")]]}),
      formControl("comment", tr("Комментарий", "Түсініктеме"), {type: "textarea"}),
    ], async data => {
      await api("/v1/analyses/" + result.id + "/review", body(Object.fromEntries(data)));
      renderOpinion(view, await api("/v1/analyses/" + result.id));
    })));
  }
  if (result.review_comment) box.append(paragraph(result.review_comment, "muted"));
  view.append(card(heading(tr("Генератор документов", "Құжаттар генераторы")), form(tr("Скачать", "Жүктеп алу"), [
    formControl("kind", tr("Документ", "Құжат"), {value: "opinion", choices: [
      ["opinion", tr("Заключение", "Қорытынды")], ["demand", tr("Претензия", "Талап хат")],
      ["complaint", tr("Жалоба", "Шағым")], ["application", tr("Заявление", "Өтініш")],
      ["lawsuit", tr("Исковое заявление", "Талап қою арызы")]]}),
    formControl("format", tr("Формат", "Пішім"), {value: "docx", choices: [["docx", "DOCX"], ["pdf", "PDF"]]}),
    formControl("addressee", tr("Адресат", "Алушы"), {required: false}),
    formControl("applicant", tr("Заявитель", "Өтініш беруші"), {required: false}),
    formControl("respondent", tr("Другая сторона", "Екінші тарап"), {required: false}),
    formControl("requests", tr("Требования, по одному на строку", "Талаптар, әр жолға біреу"), {required: false, type: "textarea"}),
  ], async data => {
    const payload = Object.fromEntries(data);
    payload.requests = payload.requests.split("\n").map(line => line.trim()).filter(Boolean);
    for (const key of ["addressee", "applicant", "respondent"]) payload[key] ||= null;
    await saveFile("/v1/analyses/" + result.id + "/export", body(payload), payload.kind + "." + payload.format);
  })));
}

async function sourcesPage(view, kind) {
  const results = card();
  const render = async data => {
    const params = new URLSearchParams({kind, language: state.lang});
    for (const [key, value] of data) if (value) params.set(key, value);
    const items = await api("/v1/sources?" + params);
    results.replaceChildren(heading(tr("Проверенный каталог", "Тексерілген каталог")), ...(items.length ? items.map(sourceItem) : [blank()]));
  };
  const controls = [formControl("q", tr("Поисковый запрос", "Іздеу сұрауы"), {required: false})];
  if (kind === "norm") controls.push(formControl("event_on", tr("На дату", "Күні бойынша"), {type: "date", required: false}));
  view.append(card(form(tr("Найти", "Іздеу"), controls, render)), results);
  await render([]);
  if (kind === "norm") {
    const exact = element("div");
    view.append(card(heading(tr("Проверка точной ссылки", "Нақты сілтемені тексеру")), form(tr("Проверить редакцию", "Редакцияны тексеру"), [
      formControl("act_id", tr("Идентификатор акта", "Акт идентификаторы")),
      formControl("article", tr("Статья", "Бап")),
      formControl("part", tr("Часть", "Бөлік"), {required: false}),
      formControl("paragraph", tr("Пункт", "Тармақ"), {required: false}),
      formControl("subparagraph", tr("Подпункт", "Тармақша"), {required: false}),
      formControl("event_on", tr("Дата события", "Оқиға күні"), {type: "date"}),
    ], async data => {
      const params = new URLSearchParams({language: state.lang});
      for (const [key, value] of data) if (value) params.set(key, value);
      const result = await api("/v1/catalogue/citation?" + params);
      exact.replaceChildren(result.source ? sourceItem(result.source) : paragraph(message(result.status)));
    }), exact));
    const acts = await api("/v1/catalogue/acts?language=" + state.lang);
    view.append(card(heading(tr("Официальные карточки актов", "Актілердің ресми карточкалары")),
      ...acts.map(act => group(external(act.url, act.title), element("small", {}, act.act_id)))));
  }
}

async function penaltyPage(view) {
  const active = state.caseId ? await api("/v1/cases/" + state.caseId) : null;
  const history = card(heading(tr("Расчёты по делу", "Іс бойынша есептеулер")));
  async function refreshHistory() {
    if (!active) return;
    const items = await api("/v1/cases/" + active.id + "/penalties");
    history.replaceChildren(heading(active.title), ...(items.length ? items.map(result =>
      element("div", {className: "list-item"}, group(result.created_at.slice(0, 10), tag(result.status)),
        paragraph(result.minimum_kzt + " – " + result.maximum_kzt + " KZT"))) : [blank()]));
  }
  if (active) {
    view.append(history);
    await refreshHistory();
  }
  const rules = await api("/v1/sources?kind=rule&language=" + state.lang);
  if (!rules.length) { view.append(card(paragraph(tr("В каталоге нет утверждённых правил расчёта.", "Каталогта бекітілген есептеу ережелері жоқ.")))); return; }
  const target = card();
  const output = element("div");
  function selectRule(id) {
    const rule = rules.find(entry => entry.id === id);
    target.replaceChildren(heading(rule.title), paragraph(rule.details.subject),
      form(active ? tr("Рассчитать и сохранить в дело", "Есептеп, іске сақтау") : tr("Рассчитать", "Есептеу"), [
        formControl("event_on", tr("Дата события", "Оқиға күні"), {type: "date", value: active?.event_on || ""}),
        formControl("decision_on", tr("Дата решения", "Шешім күні"), {type: "date", required: rule.details.mrp_date_basis === "decision"}),
        ...rule.details.conditions.map((condition, index) => formControl(String(index), condition)),
      ], async data => {
        const confirmations = Object.fromEntries(rule.details.conditions.map((condition, index) => [condition, data.get(String(index))]));
        const endpoint = active ? "/v1/cases/" + active.id + "/penalties" : "/v1/penalties";
        const result = await api(endpoint, body({rule_id: rule.id, event_on: data.get("event_on"),
          decision_on: data.get("decision_on") || null, subject: rule.details.subject, confirmations}));
        output.replaceChildren(heading(result.minimum_kzt + " – " + result.maximum_kzt + " KZT"),
          paragraph(result.formula + " · " + result.mrp + " · " + result.mrp_on),
          ...result.sources.map(sourceItem));
        await refreshHistory();
      }), output);
    output.replaceChildren();
  }
  view.append(card(group(...rules.map(rule => action(rule.title, () => selectRule(rule.id))))), target);
  selectRule(rules[0].id);
}

async function adminPage(view) {
  if (curator()) {
    const target = card();
    async function queue(status) {
      const items = await api("/v1/admin/sources?status=" + status);
      target.replaceChildren(heading(labelStatus(status)), ...(items.length ? items.map(source => {
        const snippet = element("pre");
        const controls = group(action(tr("Снимок", "Түсірілім"), async () => {
          snippet.textContent = (await api("/v1/sources/" + source.id + "/snapshot")).snapshot;
        }));
        if (status === "draft") {
          const confirmation = element("input", {type: "checkbox"});
          controls.append(element("label", {className: "check"}, confirmation, tr("Сверено с оригиналом", "Түпнұсқамен салыстырылды")),
            action(tr("Утвердить", "Бекіту"), async () => {
              if (!confirmation.checked) throw new Error(tr("Подтвердите сверку с оригиналом.", "Түпнұсқамен салыстырылғанын растаңыз."));
              await api("/v1/admin/sources/" + source.id + "/approve", body({verified: true}));
              await queue(status);
            }));
        }
        if (status === "approved") {
          controls.append(action(tr("Отозвать", "Кері қайтару"), async () => {
            if (!confirm(tr("Отозвать источник?", "Дереккөзді кері қайтару керек пе?"))) return;
            await api("/v1/admin/sources/" + source.id + "/withdraw", {method: "POST"}); await queue(status);
          }));
          if (["norm", "judgment"].includes(source.kind)) controls.append(action(tr("Индексировать", "Индекстеу"), async () => {
            await api("/v1/admin/sources/" + source.id + "/embed", {method: "POST"});
            alertMessage(tr("Источник индексирован.", "Дереккөз индекстелді."));
          }));
        }
        return element("div", {}, sourceItem(source), element("small", {}, source.id), controls, snippet);
      }) : [blank()]));
    }
    view.append(card(heading(tr("Импорт источника", "Дереккөзді импорттау")),
      paragraph(tr("Формат записи описан в OpenAPI. Для допуска требуется проверка другим сотрудником.",
        "Жазба пішімі OpenAPI-де сипатталған. Жариялау үшін басқа қызметкердің тексеруі қажет.")),
      form(tr("Импортировать черновик", "Жобаны импорттау"), [
        formControl("json", "JSON", {type: "textarea"}),
      ], async data => {
        await api("/v1/admin/sources", body(JSON.parse(data.get("json")))); await queue("draft");
      }), group(...["draft", "approved", "withdrawn"].map(status => action(labelStatus(status), () => queue(status))))), target);
    await queue("draft");
  }
  if (auditor()) {
    const audit = card(heading(tr("Журнал действий", "Әрекеттер журналы")));
    const records = await api("/v1/admin/audit");
    audit.append(...(records.length ? records.map(entry =>
      paragraph(entry.created_at.slice(0, 19) + " · " + entry.action + " · " + entry.resource)) : [blank()]));
    view.append(audit);
  }
}
locale.value = state.lang;
document.documentElement.lang = state.lang;
locale.addEventListener("change", () => {
  state.lang = locale.value;
  localStorage.setItem("zh-language", state.lang);
  document.documentElement.lang = state.lang;
  alertMessage("");
  show(state.page).catch(error => alertMessage(error.message, true));
});
show("home").catch(error => alertMessage(error.message, true));
