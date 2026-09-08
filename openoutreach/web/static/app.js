"use strict";

const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  users:
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.9M16 3a4 4 0 0 1 0 8"/><circle cx="9" cy="7" r="4"/>',
  target:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
  plug: '<path d="m8 3 2 4m6-4-2 4M6 7h12v3a6 6 0 0 1-12 0V7Zm6 9v5"/>',
  shield:
    '<path d="m12 3 8 3v5c0 5-8 10-8 10S4 16 4 11V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  settings:
    '<path d="m9 3-.5 2.3-2 1.2L4 6l-2 3 1.7 1.9v2.2L2 15l2 3 2.5-.5 2 1.2L9 21h6l.5-2.3 2-1.2 2.5.5 2-3-1.7-1.9v-2.2L22 9l-2-3-2.5.5-2-1.2L15 3Z"/><circle cx="12" cy="12" r="3"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sparkles:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  "arrow-up-right": '<path d="M6 18 18 6M6 6h12v12"/>',
  "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  at: '<circle cx="12" cy="12" r="4"/><path d="M16 8v6a2 2 0 0 0 4 0v-2a8 8 0 1 0-3 6"/>',
  send: '<path d="m22 2-7 20-4-9-9-4L22 2ZM22 2 11 13"/>',
  calendar:
    '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/>',
  route:
    '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="19" r="2"/><path d="M8 5h7a4 4 0 0 1 0 8H9a3 3 0 0 0 0 6h7"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  refresh:
    '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  whatsapp:
    '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/><path d="M8.5 10a2 2 0 0 0 2 2 5 5 0 0 0 3.5 3.5l1.5-1.5a1 1 0 0 1 1-.2 8 8 0 0 0 2.5.4 1 1 0 0 1 1 1V18a1 1 0 0 1-1 1A13 13 0 0 1 5 6a1 1 0 0 1 1-1h2.8a1 1 0 0 1 1 1 8 8 0 0 0 .4 2.5 1 1 0 0 1-.2 1z"/>',
  phone:
    '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
};
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.grid}</svg>`;
function hydrateIcons(root = document) {
  $$("[data-icon]", root).forEach((el) => {
    el.innerHTML = icon(el.dataset.icon);
  });
}
const number = (value) => new Intl.NumberFormat("pt-BR").format(value);
const initials = (value) =>
  String(value || "?")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((s) => s[0])
    .join("")
    .toUpperCase();
const formatDate = (value) =>
  value
    ? new Intl.DateTimeFormat("pt-BR", {
        day: "2-digit",
        month: "short",
        timeZone: "UTC",
      }).format(new Date(value))
    : "—";
const titles = {
  overview: "Visão geral",
  leads: "Seus leads",
  campaign: "Minha campanha",
  activity: "Atividade de e-mail",
  integrations: "Integrações",
};
const state = {
  demo: new URLSearchParams(location.search).get("demo") === "1",
  page: "overview",
  data: null,
  filter: "all",
  query: "",
  leadPage: 1,
  rows: new Map(),
  requestId: 0,
  jobStatus: "idle",
};

function makeDemo() {
  const people = [
    [
      "Marina Costa",
      "Head de Vendas",
      "Lumina",
      "marina@lumina.example",
      "Lidera uma equipe comercial de uma empresa de tecnologia em expansão. O foco em organizar a prospecção e melhorar o acompanhamento de oportunidades combina com a proposta do produto.",
    ],
    [
      "Rafael Mendes",
      "CEO & Cofundador",
      "Orbit",
      "rafael@orbit.example",
      "Cofundador de uma empresa SaaS que está estruturando sua operação comercial. O perfil é compatível com soluções que ajudam equipes enxutas a desenvolver novas oportunidades.",
    ],
    [
      "Camila Oliveira",
      "Diretora Comercial",
      "Nexo",
      "camila@nexo.example",
      "Responsável pela estratégia comercial de uma empresa B2B. O trabalho com equipes de vendas e processos de relacionamento atende ao público descrito na campanha.",
    ],
    [
      "Lucas Almeida",
      "VP de Growth",
      "Forma",
      null,
      "Lidera iniciativas de crescimento em uma empresa de software. Tem aderência ao perfil de quem busca ampliar a prospecção com mais contexto sobre os potenciais clientes.",
    ],
    [
      "Beatriz Santos",
      "Head de Revenue",
      "Vértice",
      "beatriz@vertice.example",
      "Atua na integração entre marketing e vendas de uma empresa de tecnologia. A necessidade de acompanhar oportunidades está alinhada ao problema que o produto resolve.",
    ],
    [
      "Pedro Lima",
      "Diretor de Operações",
      "Arco",
      null,
      "Coordena processos de uma operação B2B em crescimento e pode se beneficiar de uma visão unificada do relacionamento com clientes.",
    ],
    [
      "Ana Martins",
      "Gerente Comercial",
      "Prisma",
      "ana@prisma.example",
      "Gerencia uma equipe de vendas de software e trabalha com melhoria de processos comerciais, em linha com o público da campanha.",
    ],
    [
      "Guilherme Rocha",
      "Cofundador",
      "Ponto",
      null,
      "Participa das decisões de expansão comercial de uma empresa de tecnologia e tem perfil aderente à proposta da campanha.",
    ],
  ];
  const today = new Date();
  const dateAt = (i) => {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    return d.toISOString();
  };
  const demoPhones = [
    "+55 (11) 98765-4321",
    "+55 (11) 99123-4567",
    "+55 (21) 99876-5432",
    "+55 (31) 98712-3456",
    "+55 (41) 99654-3210",
    "+55 (11) 98111-2233",
    "+55 (51) 99345-6789",
    "+55 (19) 98456-7890",
  ];
  const rows = people.map(([name, title, company, email, reason], i) => ({
    name,
    title,
    company,
    email,
    whatsapp: demoPhones[i] || "",
    whatsapp_url: demoPhones[i]
      ? `https://wa.me/${demoPhones[i].replace(/\D/g, "")}`
      : "",
    reason,
    lead_id: `demo-${i}`,
    first_name: name.split(" ")[0],
    last_name: name.split(" ").slice(1).join(" "),
    qualified_at: dateAt(i),
    website: "",
    linkedin_url: "",
  }));
  const discovered = [2, 3, 1, 4, 3, 6, 4, 8, 5, 9, 6, 11, 10, 12];
  return {
    stats: {
      discovered: 84,
      qualified: 8,
      email: 5,
      whatsapp: 8,
      sent: 16,
      replies: 3,
    },
    recent: rows.slice(0, 5),
    demoRows: rows,
    chart: discovered.map((value, i) => ({
      date: dateAt(13 - i).slice(0, 10),
      discovered: value,
      qualified: i >= 6 ? 1 : 0,
    })),
    activity: rows
      .slice(0, 4)
      .map((r, i) => ({
        id: i,
        subject:
          i === 0
            ? "Re: Uma ideia para a Lumina"
            : `Uma ideia para a ${r.company}`,
        direction: i === 0 ? "in" : "out",
        kind: i === 0 ? "human_reply" : "outbound",
        to_address: r.email || "contato@forma.example",
        from_address: r.email,
        recorded_at: dateAt(i),
        sent_at: dateAt(i),
      })),
    config: {
      product_docs:
        "Uma plataforma de CRM que ajuda equipes comerciais a organizar contatos, acompanhar oportunidades e criar relacionamentos melhores com seus potenciais clientes.",
      campaign_target:
        "Diretores comerciais, heads de vendas e fundadores de empresas de tecnologia B2B no Brasil, com equipes de vendas em crescimento.",
      booking_link: "",
      signature: "Equipe OpenOutreach",
      operator_name: "Meu workspace",
      operator_email: "",
      operator_country_code: "BR",
      ai_model: "",
      llm_api_base: "",
      mailbox_address: "",
      smtp_host: "",
      smtp_port: "",
      imap_host: "",
      imap_port: "",
      accepted_legal_notice: false,
      configured: {
        llm_api_key: false,
        bettercontact_api_key: false,
        mailbox_password: false,
      },
      free_provider_active: true,
      is_resend: false,
    },
  };
}

async function api(url, options = {}) {
  const headers = { ...options.headers };
  if (options.method === "POST") {
    headers["X-CSRFToken"] = decodeURIComponent(
      document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrftoken="))
        ?.split("=")
        .slice(1)
        .join("=") || "",
    );
  }
  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(
      "O servidor não respondeu como esperado. Atualize a página e tente novamente.",
    );
  }
  if (!response.ok) {
    const errors = body.errors
      ? Object.entries(body.errors)
          .flatMap(([field, values]) =>
            values.map((v) => `${field}: ${v.message}`),
          )
          .join(" ")
      : body.error;
    throw new Error(
      errors ||
        "Não foi possível concluir. Atualize a página e tente novamente.",
    );
  }
  return body;
}

let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 5000);
}

function empty(title, description, action = true) {
  return `<div class="empty-state"><span class="empty-icon">${icon("users")}</span><h3>${esc(title)}</h3><p>${esc(description)}</p>${action ? '<a class="button" href="#campaign">Configurar minha campanha ' + icon("arrow") + "</a>" : ""}</div>`;
}

function table(rows) {
  if (!rows.length)
    return empty(
      state.query || state.filter !== "all"
        ? "Nenhum lead com esse filtro"
        : "O próximo bom contato está por vir",
      state.query || state.filter !== "all"
        ? "Tente outro nome, empresa ou filtro para encontrar seus leads."
        : "Defina sua campanha e inicie uma busca. Cada pessoa qualificada aparecerá aqui, junto com o motivo do match.",
      !state.query && state.filter === "all",
    );
  rows.forEach((row) => state.rows.set(String(row.lead_id), row));
  return `<table><thead><tr><th>CONTATO</th><th>EMPRESA</th><th>E-MAIL</th><th>WHATSAPP</th><th>QUALIFICADO EM</th><th><span class="sr-only">Detalhes</span></th></tr></thead><tbody>${rows
    .map(
      (row, i) =>
        `<tr><td><div class="person"><span class="person-avatar tone-${i % 4}">${esc(initials(row.name))}</span><span><strong>${esc(row.name)}</strong><small>${esc(row.title || "Cargo não informado")}</small></span></div></td><td>${esc(row.company || "Não informada")}</td><td><span class="status-badge ${row.email ? "" : "pending"}">${row.email ? "Disponível" : "Não encontrado"}</span></td><td>${row.whatsapp ? `<a href="${esc(row.whatsapp_url || `https://wa.me/${row.whatsapp.replace(/\D/g, "")}`)}" target="_blank" rel="noopener noreferrer" class="wa-badge" title="Abrir conversa no WhatsApp">${icon("whatsapp")} ${esc(row.whatsapp)}</a>` : '<span class="status-badge pending">Não cadastrado</span>'}</td><td>${formatDate(row.qualified_at)}</td><td><button class="detail-button" data-lead="${esc(row.lead_id)}" aria-label="Ver detalhes de ${esc(row.name)}">Ver contexto ${icon("arrow")}</button></td></tr>`,
    )
    .join("")}</tbody></table>`;
}

function renderChart(chart) {
  const max = Math.max(
    4,
    ...chart.map((d) => Math.max(d.discovered, d.qualified)),
  );
  const ceiling = Math.ceil(max / 4) * 4;
  const width = 560,
    height = 140,
    left = 30,
    top = 10,
    bottom = 115;
  const x = (i) =>
    left + (i * (width - left - 8)) / Math.max(1, chart.length - 1);
  const y = (value) => bottom - (value / ceiling) * (bottom - top);
  const points = (field) =>
    chart.map((d, i) => `${x(i)},${y(d[field])}`).join(" ");
  const grid = Array.from({ length: 5 }, (_, i) => {
    const value = (i * ceiling) / 4;
    return `<line class="gridline" x1="${left}" x2="${width}" y1="${y(value)}" y2="${y(value)}"/><text x="20" y="${y(value) + 3}" text-anchor="end">${value}</text>`;
  }).join("");
  const labels = chart
    .filter((_, i) => i % 3 === 0 || i === chart.length - 1)
    .map((d) => {
      const i = chart.indexOf(d);
      return `<text x="${x(i)}" y="135" text-anchor="${i === 0 ? "start" : i === chart.length - 1 ? "end" : "middle"}">${formatDate(d.date).replace(" de ", " ")}</text>`;
    })
    .join("");
  const hasData = chart.some((d) => d.discovered || d.qualified);
  $("#chart").innerHTML =
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${hasData ? "Leads por dia nos últimos 14 dias" : "Ainda não há leads nos últimos 14 dias"}"><title>Leads por dia</title>${grid}<polygon class="area" points="${left},${bottom} ${points("qualified")} ${x(chart.length - 1)},${bottom}"/><polyline class="discovered-line" points="${points("discovered")}"/><polyline class="qualified-line" points="${points("qualified")}"/>${labels}</svg>${hasData ? "" : '<span class="chart-empty">Suas próximas descobertas vão aparecer aqui</span>'}`;
}

function renderDashboard() {
  const { stats, config, recent, chart, activity } = state.data;
  for (const key of ["discovered", "qualified", "email", "whatsapp", "sent"]) {
    const el = $(`#stat-${key}`);
    if (el) el.textContent = number(stats[key] ?? 0);
  }
  $("#nav-count").textContent = number(stats.qualified);
  $("#qualified-foot").innerHTML = stats.discovered
    ? `<em>${Math.round((stats.qualified / stats.discovered) * 100)}% do total</em> compatíveis com seu público`
    : "Compatíveis com seu público";
  $("#sent-foot").innerHTML = stats.replies
    ? `<em>${number(stats.replies)} respostas</em> de pessoas reais`
    : "Conversas em movimento";
  $("#funnel").innerHTML = [
    ["Pessoas descobertas", stats.discovered],
    ["Matches qualificados", stats.qualified],
    ["E-mails disponíveis", stats.email],
    ["WhatsApp encontrados", stats.whatsapp ?? stats.qualified],
    ["Mensagens enviadas", stats.sent],
  ]
    .map(
      ([label, value], i) =>
        `<div class="funnel-row"><span class="step-number">0${i + 1}</span><span>${label}</span><strong>${number(value)}</strong></div>`,
    )
    .join("");
  $("#recent-leads").innerHTML = table(recent);
  renderChart(chart);
  $("#activity-list").innerHTML = activity.length
    ? activity
        .map(
          (row) =>
            `<article class="activity-row"><span class="integration-icon ${row.direction === "in" ? "green" : "violet"}">${icon(row.direction === "in" ? "mail" : "send")}</span><div><h3>${esc(row.subject || "Sem assunto")}</h3><p>${row.direction === "in" ? "Recebido de" : "Enviado para"} ${esc(row.direction === "in" ? row.from_address : row.to_address)}${row.kind === "human_reply" ? " · Resposta humana" : ""}</p></div><time datetime="${esc(row.sent_at || row.recorded_at)}">${formatDate(row.sent_at || row.recorded_at)}</time></article>`,
        )
        .join("")
    : empty(
        "Suas conversas vão aparecer aqui",
        "O histórico é atualizado a partir dos envios e respostas registrados pelo OpenOutSend.",
        false,
      );
  const name = config.operator_name || "Meu workspace";
  $("#operator-avatar").textContent = initials(config.operator_name || "Eu");
  $("#operator-label").innerHTML = `${esc(name)}<small>Conta pessoal</small>`;
  $("#demo-banner").hidden = !state.demo;
  if (state.demo) $("#job-banner").hidden = true;
  $("#demo-toggle").setAttribute("aria-pressed", String(state.demo));
  $("#demo-toggle").innerHTML =
    `<span class="demo-dot"></span>${state.demo ? "Demonstração ativa" : "Explorar demonstração"}`;
}

function populateForms(only) {
  const config = state.data.config;
  for (const id of ["campaign-form", "integrations-form"]) {
    if (only && id !== only) continue;
    const form = $(`#${id}`);
    $$("input,textarea", form).forEach((el) => {
      if (el.type === "password") {
        el.value = "";
        el.placeholder = config.configured[el.name]
          ? "Chave salva · deixe em branco para manter"
          : el.name === "mailbox_password"
            ? "Senha de aplicativo"
            : "Sua chave de API";
      } else if (el.type === "checkbox") el.checked = Boolean(config[el.name]);
      else el.value = config[el.name] || "";
      el.disabled = state.demo;
    });
    $("button[type=submit]", form).disabled = state.demo;
    $(".form-feedback", form).textContent = state.demo
      ? "A edição está disponível no seu workspace real."
      : "";
  }
  const llmConnected = Boolean(config.configured.llm_api_key);
  const llmPill = $("#llm-status");
  if (llmPill) {
    llmPill.textContent = llmConnected ? "Credencial salva" : "Não configurado";
    llmPill.className = `pill ${llmConnected ? "success" : "neutral"}`;
  }

  const providerPill = $("#provider-status");
  if (providerPill) {
    const hasBc = Boolean(config.configured.bettercontact_api_key);
    if (hasBc) {
      providerPill.textContent = "BetterContact ativo";
      providerPill.className = "pill success";
    } else {
      providerPill.textContent = "Provedor Gratuito ativo";
      providerPill.className = "pill success";
    }
  }

  const mailboxPill = $("#mailbox-status");
  if (mailboxPill) {
    const hasPass = Boolean(config.configured.mailbox_password);
    if (hasPass) {
      mailboxPill.textContent = config.is_resend ? "Resend ativo" : "SMTP ativo";
      mailboxPill.className = "pill success";
    } else {
      mailboxPill.textContent = "Não configurado";
      mailboxPill.className = "pill neutral";
    }
  }
}

async function load({ forms = false } = {}) {
  try {
    const wasDemo = state.demo;
    const data = wasDemo ? makeDemo() : await api("/api/dashboard");
    if (state.demo !== wasDemo) return;
    state.data = data;
    $("#global-error").hidden = true;
    renderDashboard();
    if (forms) populateForms();
    if (state.page === "leads") await loadLeads();
  } catch (error) {
    $("#global-error").hidden = false;
    $("#global-error span").textContent = error.message;
  }
}

async function loadLeads() {
  const requestId = ++state.requestId;
  $("#all-leads").setAttribute("aria-busy", "true");
  try {
    let result;
    if (state.demo) {
      const query = state.query.toLocaleLowerCase("pt-BR");
      const rows = state.data.demoRows.filter(
        (r) =>
          (!query ||
            `${r.name} ${r.company} ${r.title} ${r.email || ""} ${r.whatsapp || ""}`
              .toLocaleLowerCase("pt-BR")
              .includes(query)) &&
          (state.filter === "all" ||
            (state.filter === "email" && Boolean(r.email)) ||
            (state.filter === "whatsapp" && Boolean(r.whatsapp))),
      );
      result = {
        rows: rows.slice((state.leadPage - 1) * 20, state.leadPage * 20),
        total: rows.length,
      };
    } else
      result = await api(
        `/api/leads?${new URLSearchParams({ q: state.query, status: state.filter, page: state.leadPage })}`,
      );
    if (requestId !== state.requestId) return;
    $("#all-leads").innerHTML = table(result.rows);
    $("#lead-total").textContent =
      `${number(result.total)} ${result.total === 1 ? "lead encontrado" : "leads encontrados"}`;
    $("#page-number").textContent = state.leadPage;
    $("#prev-page").disabled = state.leadPage === 1;
    $("#next-page").disabled = state.leadPage * 20 >= result.total;
  } catch (error) {
    $("#all-leads").innerHTML = empty(
      "Não foi possível carregar os leads",
      error.message,
      false,
    );
    $("#lead-total").textContent = "Falha ao carregar";
    $("#prev-page").disabled = true;
    $("#next-page").disabled = true;
  } finally {
    if (requestId === state.requestId)
      $("#all-leads").removeAttribute("aria-busy");
  }
}

function navigate() {
  if (location.hash === "#main") {
    $("#main").focus();
    return;
  }
  state.page = Object.hasOwn(titles, location.hash.slice(1))
    ? location.hash.slice(1)
    : "overview";
  $$(".page").forEach((el) => {
    el.hidden = el.id !== `page-${state.page}`;
  });
  $$("[data-page]").forEach((el) => {
    const active = el.dataset.page === state.page;
    el.classList.toggle("active", active);
    active
      ? el.setAttribute("aria-current", "page")
      : el.removeAttribute("aria-current");
  });
  $("#breadcrumb-title").textContent = titles[state.page];
  document.title = `${titles[state.page]} · OpenOutreach`;
  if (state.page === "leads" && state.data) loadLeads();
  window.scrollTo(0, 0);
}

function showLead(id) {
  const row = state.rows.get(id);
  if (!row) return;
  const waUrl =
    row.whatsapp_url ||
    (row.whatsapp ? `https://wa.me/${row.whatsapp.replace(/\D/g, "")}` : "");
  $("#lead-detail").innerHTML =
    `<span class="person-avatar detail-avatar">${esc(initials(row.name))}</span><h2 id="lead-detail-title">${esc(row.name)}</h2><p class="detail-subtitle">${esc(row.title || "Cargo não informado")}<br>${esc(row.company || "Empresa não informada")}</p><div class="detail-reason"><h3>${icon("sparkles")}Por que este lead combina com você</h3><p>${esc(row.reason || "Nenhum motivo registrado.")}</p></div><div class="detail-field"><span>E-mail profissional</span><strong>${esc(row.email || "Não encontrado")}</strong></div><div class="detail-field"><span>WhatsApp / Celular</span><strong>${row.whatsapp ? `<a href="${esc(waUrl)}" target="_blank" rel="noopener noreferrer" class="wa-badge" style="display:inline-flex;padding:4px 10px;font-size:14px;">${icon("whatsapp")} ${esc(row.whatsapp)}</a>` : "Não cadastrado"}</strong></div><div class="detail-field"><span>Qualificado em</span><strong>${formatDate(row.qualified_at)}</strong></div><div class="dialog-footer" style="display:flex;gap:8px;flex-wrap:wrap;">${row.email ? '<button class="button" id="copy-email">' + icon("at") + "Copiar e-mail</button>" : ""}${row.whatsapp ? '<a class="button button-primary" id="open-whatsapp" href="' + esc(waUrl) + '" target="_blank" rel="noopener noreferrer" style="background:#25D366;border-color:#25D366;color:#fff;">' + icon("whatsapp") + "Conversar no WhatsApp ↗</a>" : ""}</div>`;
  $("#lead-dialog").setAttribute("aria-labelledby", "lead-detail-title");
  $("#lead-dialog").showModal();
  $("#copy-email")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(row.email);
      toast("E-mail copiado.");
    } catch {
      toast("Não foi possível copiar. Selecione o endereço para copiá-lo.");
    }
  });
}

async function toggleDemo(value) {
  state.demo = value;
  state.leadPage = 1;
  state.rows.clear();
  state.requestId++;
  const url = new URL(location.href);
  value ? url.searchParams.set("demo", "1") : url.searchParams.delete("demo");
  history.replaceState(null, "", url);
  await load({ forms: true });
}

for (const [id, section] of [
  ["campaign-form", "campaign"],
  ["integrations-form", "integrations"],
]) {
  $(`#${id}`).addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.demo) return;
    const form = event.currentTarget,
      button = $("button[type=submit]", form),
      feedback = $(".form-feedback", form);
    button.disabled = true;
    feedback.textContent = "Salvando…";
    feedback.classList.remove("success");
    try {
      const config = await api(`/api/config/${section}`, {
        method: "POST",
        body: new FormData(form),
      });
      state.data.config = config;
      populateForms(id);
      feedback.classList.add("success");
      feedback.textContent = "Alterações salvas no seu workspace.";
      renderDashboard();
      toast(
        section === "campaign"
          ? "Campanha salva. Seu contexto está pronto."
          : "Integrações salvas. As credenciais serão verificadas na execução.",
      );
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      button.disabled = state.demo;
    }
  });
}

function openFind() {
  if (state.demo)
    return toast(
      "Volte ao seu workspace para iniciar uma busca com seus serviços.",
    );
  if (!state.data) return toast("Aguarde o carregamento do workspace.");
  if (!state.data.config.product_docs || !state.data.config.campaign_target) {
    location.hash = "campaign";
    return toast("Descreva seu produto e seu público para começar.");
  }
  $("#find-dialog").showModal();
}

$("#find-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.demo) return;
  const button = $("button[type=submit]", event.currentTarget),
    feedback = $(".form-feedback", event.currentTarget);
  button.disabled = true;
  feedback.textContent = "";
  try {
    const job = await api("/api/job", {
      method: "POST",
      body: new FormData(event.currentTarget),
    });
    renderJob(job);
    $("#find-dialog").close();
    toast("Busca iniciada. Os novos leads aparecem conforme são qualificados.");
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

function renderJob(job) {
  const previous = state.jobStatus;
  state.jobStatus = job.status;
  const messages = {
    running: `Buscando ${job.count} novos leads. Você pode continuar usando o painel.`,
    completed: "Busca concluída. Seus leads estão atualizados.",
    failed: job.error
      ? `A busca não foi concluída: ${job.error}`
      : "A busca não foi concluída. Revise as credenciais e o aviso legal em Integrações. Para ver o diagnóstico, execute a busca no terminal.",
    cancelled: "Busca interrompida. Os leads já encontrados foram preservados.",
    timeout:
      job.error || "A busca atingiu o limite de 15 minutos. Os leads encontrados foram preservados.",
  };
  $("#job-banner").hidden = state.demo || job.status === "idle";
  $("#job-text").textContent = messages[job.status] || "";
  $("#cancel-job").hidden = job.status !== "running";
  if (previous === "running" && job.status !== "running") load();
}

$("#cancel-job").addEventListener("click", async () => {
  try {
    renderJob(
      await api("/api/job", {
        method: "POST",
        body: new URLSearchParams({ action: "cancel" }),
      }),
    );
  } catch (error) {
    toast(error.message);
  }
});
document.addEventListener("click", (event) => {
  if (event.target.closest('[data-action="find"]')) openFind();
  const detail = event.target.closest("[data-lead]");
  if (detail) showLead(detail.dataset.lead);
  const close = event.target.closest("[data-close]");
  if (close) close.closest("dialog").close();
});
$("#find-dialog").setAttribute("aria-label", "Nova busca de leads");
$$("dialog").forEach((dialog) =>
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        dialog.close();
    }
  }),
);
$("#demo-toggle").addEventListener("click", () => toggleDemo(!state.demo));
$("#exit-demo").addEventListener("click", () => toggleDemo(false));
$("#retry").addEventListener("click", () => load({ forms: !state.data }));
$("#refresh-activity").addEventListener("click", async () => {
  await load();
  if ($("#global-error").hidden) toast("Atividade atualizada.");
});
let searchTimer;
$("#lead-search").addEventListener("input", (event) => {
  state.query = event.target.value;
  state.leadPage = 1;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadLeads, 220);
});
$$("[data-filter]").forEach((button) =>
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    state.leadPage = 1;
    $$("[data-filter]").forEach((b) => {
      b.classList.toggle("active", b === button);
      b.setAttribute("aria-pressed", String(b === button));
    });
    loadLeads();
  }),
);
$("#prev-page").addEventListener("click", () => {
  state.leadPage = Math.max(1, state.leadPage - 1);
  loadLeads();
});
$("#next-page").addEventListener("click", () => {
  state.leadPage++;
  loadLeads();
});
$("#enrich-whatsapp-btn")?.addEventListener("click", async () => {
  if (state.demo)
    return toast("O enriquecimento de WhatsApp está disponível no seu workspace real.");
  const btn = $("#enrich-whatsapp-btn");
  btn.disabled = true;
  const originalHtml = btn.innerHTML;
  btn.innerHTML = `${icon("refresh")} Enriquecendo…`;
  try {
    const res = await api("/api/enrich/whatsapp", { method: "POST" });
    toast(`Enriquecimento concluído: ${res.enriched} leads atualizados.`);
    await load();
  } catch (err) {
    toast(`Erro no enriquecimento: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
    hydrateIcons(btn);
  }
});
$("#export").addEventListener("click", () => {
  if (state.demo)
    return toast("A exportação está disponível no seu workspace real.");
  location.href = `/api/export?${new URLSearchParams({ q: state.query, status: state.filter })}`;
});
window.addEventListener("hashchange", navigate);
hydrateIcons();
navigate();
load({ forms: true });
setInterval(async () => {
  if (document.hidden || state.demo) return;
  try {
    const job = await api("/api/job");
    renderJob(job);
    if (job.status === "running") await load();
  } catch {
    if (state.jobStatus === "running") {
      $("#job-text").textContent =
        "Conexão interrompida. Tentando atualizar o andamento da busca…";
    }
  }
}, 4000);
