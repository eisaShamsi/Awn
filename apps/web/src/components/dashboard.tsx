"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";
import type {
  Conversation,
  Message,
  Run,
  RunStatus,
  SetupState,
  Workspace,
} from "@/lib/types";

type ScreenState = "loading" | "setup" | "ready" | "offline";

const RUN_LABELS: Record<RunStatus, string> = {
  received: "مستلم",
  planning: "يخطط",
  needs_clarification: "يحتاج توضيحاً",
  ready: "جاهز",
  awaiting_approval: "بانتظار الموافقة",
  executing: "قيد التنفيذ",
  verifying: "يتحقق",
  succeeded: "مكتمل",
  partially_succeeded: "مكتمل جزئياً",
  failed: "فشل",
  denied: "مرفوض",
  cancelled: "ملغي",
};

const TERMINAL_RUNS = new Set<RunStatus>([
  "succeeded",
  "partially_succeeded",
  "failed",
  "denied",
  "cancelled",
]);

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("ar-AE", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function messageText(message: Message): string {
  return message.parts
    .map((part) => part.text ?? (part.data ? JSON.stringify(part.data) : ""))
    .filter(Boolean)
    .join("\n");
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "حدث خطأ غير متوقع. حاول مرة أخرى.";
}

export function Dashboard() {
  const [screen, setScreen] = useState<ScreenState>("loading");
  const [setup, setSetup] = useState<SetupState | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<string>("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string>("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const loadConversation = useCallback(async (nextWorkspaceId: string, nextConversationId: string) => {
    if (!nextWorkspaceId || !nextConversationId) {
      setMessages([]);
      setRuns([]);
      return;
    }
    const base = `workspaces/${nextWorkspaceId}/conversations/${nextConversationId}`;
    const [nextMessages, nextRuns] = await Promise.all([
      apiRequest<Message[]>(`${base}/messages`),
      apiRequest<Run[]>(`${base}/runs`),
    ]);
    setMessages(nextMessages);
    setRuns(nextRuns);
  }, []);

  const loadWorkspace = useCallback(async (nextWorkspaceId: string) => {
    if (!nextWorkspaceId) return;
    const nextConversations = await apiRequest<Conversation[]>(
      `workspaces/${nextWorkspaceId}/conversations`,
    );
    setConversations(nextConversations);
    const nextConversationId = nextConversations[0]?.id ?? "";
    setConversationId(nextConversationId);
    if (nextConversationId) {
      await loadConversation(nextWorkspaceId, nextConversationId);
    } else {
      setMessages([]);
      setRuns([]);
    }
  }, [loadConversation]);

  const initialize = useCallback(async () => {
    setScreen("loading");
    setNotice(null);
    try {
      await apiRequest<{ status: "ready" }>("ready");
      let current: SetupState;
      try {
        current = await apiRequest<SetupState>("setup");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          setScreen("setup");
          return;
        }
        throw error;
      }
      const nextWorkspaces = await apiRequest<Workspace[]>("workspaces");
      setSetup(current);
      setWorkspaces(nextWorkspaces);
      const nextWorkspaceId = nextWorkspaces[0]?.id ?? current.workspace.id;
      setWorkspaceId(nextWorkspaceId);
      await loadWorkspace(nextWorkspaceId);
      setScreen("ready");
    } catch (error) {
      setNotice(errorMessage(error));
      setScreen("offline");
    }
  }, [loadWorkspace]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void initialize(), 0);
    return () => window.clearTimeout(timeout);
  }, [initialize]);

  useEffect(() => {
    if (!workspaceId || !conversationId) return;
    const interval = window.setInterval(() => {
      const base = `workspaces/${workspaceId}/conversations/${conversationId}`;
      void apiRequest<Run[]>(`${base}/runs`).then(setRuns).catch(() => undefined);
    }, 5_000);
    return () => window.clearInterval(interval);
  }, [conversationId, workspaceId]);

  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === conversationId) ?? null,
    [conversationId, conversations],
  );
  const activeRuns = runs.filter((run) => !TERMINAL_RUNS.has(run.status)).length;

  async function selectWorkspace(nextWorkspaceId: string) {
    setWorkspaceId(nextWorkspaceId);
    setConversationId("");
    setConversations([]);
    setMessages([]);
    setRuns([]);
    setNotice(null);
    try {
      await loadWorkspace(nextWorkspaceId);
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function selectConversation(nextConversationId: string) {
    setConversationId(nextConversationId);
    setMessages([]);
    setRuns([]);
    setNotice(null);
    try {
      await loadConversation(workspaceId, nextConversationId);
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function submitSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    const form = new FormData(event.currentTarget);
    try {
      await apiRequest<SetupState>("setup", {
        method: "POST",
        body: JSON.stringify({
          display_name: form.get("display_name"),
          workspace_name: form.get("workspace_name"),
          locale: "ar",
          timezone: "Asia/Dubai",
        }),
      });
      await initialize();
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function createConversation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    setBusy(true);
    setNotice(null);
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const conversation = await apiRequest<Conversation>(
        `workspaces/${workspaceId}/conversations`,
        {
          method: "POST",
          body: JSON.stringify({ title: data.get("title") }),
        },
      );
      setConversations((current) => [conversation, ...current]);
      setConversationId(conversation.id);
      setMessages([]);
      setRuns([]);
      form.reset();
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!workspaceId || !conversationId || !text) return;
    setBusy(true);
    setNotice(null);
    const base = `workspaces/${workspaceId}/conversations/${conversationId}`;
    try {
      const message = await apiRequest<Message>(`${base}/messages`, {
        method: "POST",
        body: JSON.stringify({ parts: [{ type: "text", text }] }),
      });
      setMessages((current) => [...current, message]);
      setDraft("");

      const run = await apiRequest<Run>(`${base}/runs`, {
        method: "POST",
        body: JSON.stringify({ request_message_id: message.id, autonomy_level: 0 }),
      });
      setRuns((current) => [run, ...current]);
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  if (screen === "loading") {
    return (
      <main className="centered-state">
        <div className="brand-mark large">ع</div>
        <div className="loader" aria-label="جارٍ تحميل عَوْن" />
        <p>جارٍ الاتصال بنواة عَوْن…</p>
      </main>
    );
  }

  if (screen === "offline") {
    return (
      <main className="centered-state">
        <div className="brand-mark large">ع</div>
        <span className="eyebrow">الخدمة غير متاحة</span>
        <h1>تعذر الوصول إلى نواة عَوْن</h1>
        <p>{notice}</p>
        <p className="muted">تأكد من تشغيل FastAPI على المنفذ 8000 ثم أعد المحاولة.</p>
        <button className="primary-button" onClick={() => void initialize()}>
          إعادة الاتصال
        </button>
      </main>
    );
  }

  if (screen === "setup") {
    return (
      <main className="onboarding-shell">
        <section className="onboarding-copy">
          <div className="brand-lockup">
            <div className="brand-mark large">ع</div>
            <div>
              <strong>عَوْن</strong>
              <span>مساعدك الذكي الخاص</span>
            </div>
          </div>
          <span className="eyebrow">التهيئة الأولى</span>
          <h1>لنُنشئ مساحتك الخاصة.</h1>
          <p>
            تُحفظ بياناتك محلياً في PostgreSQL، وتبقى الإجراءات تحت مستوى التفويض الذي
            تحدده أنت.
          </p>
          <ul className="promise-list">
            <li>مساحات عمل معزولة</li>
            <li>تشغيل قابل للتتبع</li>
            <li>موافقة قبل الأثر الحساس</li>
          </ul>
        </section>
        <section className="onboarding-card">
          <div>
            <span className="step-label">الخطوة 1 من 1</span>
            <h2>عرّف عَوْن بك</h2>
            <p>سنضيف إمكانية تعديل هذه البيانات لاحقاً من الإعدادات.</p>
          </div>
          <form onSubmit={submitSetup} className="stacked-form">
            <label>
              الاسم المعروض
              <input name="display_name" required maxLength={200} placeholder="مثال: عيسى" />
            </label>
            <label>
              اسم مساحة العمل الأولى
              <input
                name="workspace_name"
                required
                maxLength={200}
                defaultValue="مساحة عَوْن"
              />
            </label>
            {notice && <p className="error-notice">{notice}</p>}
            <button className="primary-button wide" disabled={busy}>
              {busy ? "جارٍ الإنشاء…" : "إنشاء مساحتي"}
            </button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup compact">
          <div className="brand-mark">ع</div>
          <div>
            <strong>عَوْن</strong>
            <span>المساعد الخاص</span>
          </div>
        </div>

        <nav aria-label="التنقل الرئيسي">
          <button
            className="nav-item active"
            type="button"
            aria-current="page"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          >
            <span>⌂</span>نظرة عامة
          </button>
          <button
            className="nav-item"
            type="button"
            onClick={() => document.getElementById("conversations")?.scrollIntoView({ behavior: "smooth" })}
          >
            <span>◫</span>المحادثات
          </button>
          <button className="nav-item disabled" disabled><span>✓</span>المهام<small>قريباً</small></button>
          <button className="nav-item disabled" disabled><span>↻</span>التشغيلات<small>قريباً</small></button>
        </nav>

        <div className="sidebar-footer">
          <div className="local-badge"><span /> تشغيل محلي آمن</div>
          <div className="profile-chip">
            <div className="avatar">{setup?.user.display_name.slice(0, 1)}</div>
            <div>
              <strong>{setup?.user.display_name}</strong>
              <span>{setup?.user.timezone}</span>
            </div>
          </div>
        </div>
      </aside>

      <main className="dashboard-main">
        <header className="topbar">
          <div>
            <span className="eyebrow">لوحة التحكم</span>
            <h1>مرحباً، {setup?.user.display_name}</h1>
            <p>تابع أعمالك وتواصل مع عَوْن من مكان واحد.</p>
          </div>
          <div className="topbar-actions">
            <label className="workspace-picker">
              <span>مساحة العمل</span>
              <select
                value={workspaceId}
                onChange={(event) => void selectWorkspace(event.target.value)}
              >
                {workspaces.map((workspace) => (
                  <option value={workspace.id} key={workspace.id}>{workspace.name}</option>
                ))}
              </select>
            </label>
            <div className="service-status"><span /> متصل</div>
          </div>
        </header>

        {notice && (
          <div className="inline-notice" role="status">
            <span>{notice}</span>
            <button onClick={() => setNotice(null)} aria-label="إغلاق التنبيه">×</button>
          </div>
        )}

        <section className="stats-grid" aria-label="ملخص العمل">
          <article className="stat-card accent">
            <span className="stat-label">مساحات العمل</span>
            <strong>{workspaces.length}</strong>
            <small>نطاقات مستقلة لبياناتك</small>
          </article>
          <article className="stat-card">
            <span className="stat-label">المحادثات</span>
            <strong>{conversations.length}</strong>
            <small>في المساحة الحالية</small>
          </article>
          <article className="stat-card">
            <span className="stat-label">التشغيلات النشطة</span>
            <strong>{activeRuns}</strong>
            <small>{activeRuns ? "تُحدّث كل خمس ثوانٍ" : "لا يوجد عمل معلّق"}</small>
          </article>
        </section>

        <section className="workspace-grid" id="conversations">
          <aside className="conversation-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">المحادثات</span>
                <h2>جلسات العمل</h2>
              </div>
              <span className="count-badge">{conversations.length}</span>
            </div>
            <form className="new-conversation" onSubmit={createConversation}>
              <input name="title" maxLength={300} placeholder="عنوان محادثة جديدة" required />
              <button disabled={busy} aria-label="إنشاء محادثة">＋</button>
            </form>
            <div className="conversation-list">
              {conversations.length === 0 ? (
                <div className="empty-mini">
                  <span>◌</span>
                  <p>لا توجد محادثات بعد.</p>
                </div>
              ) : conversations.map((conversation) => (
                <button
                  key={conversation.id}
                  className={`conversation-row ${conversation.id === conversationId ? "selected" : ""}`}
                  onClick={() => void selectConversation(conversation.id)}
                >
                  <span className="conversation-icon">ع</span>
                  <span className="conversation-copy">
                    <strong>{conversation.title ?? "محادثة بلا عنوان"}</strong>
                    <small>{formatTime(conversation.updated_at)}</small>
                  </span>
                </button>
              ))}
            </div>
          </aside>

          <section className="chat-panel">
            {selectedConversation ? (
              <>
                <header className="chat-heading">
                  <div>
                    <span className="eyebrow">المحادثة الحالية</span>
                    <h2>{selectedConversation.title ?? "محادثة بلا عنوان"}</h2>
                  </div>
                  {runs[0] && (
                    <span className={`run-badge status-${runs[0].status}`}>
                      {RUN_LABELS[runs[0].status]}
                    </span>
                  )}
                </header>

                <div className="messages" aria-live="polite">
                  {messages.length === 0 ? (
                    <div className="chat-empty">
                      <div className="brand-mark large">ع</div>
                      <h3>كيف يمكنني معاونتك؟</h3>
                      <p>اكتب طلبك، وسيُحفظ كتَشغيل قابل للمتابعة والتدقيق.</p>
                    </div>
                  ) : messages.map((message) => (
                    <article key={message.id} className={`message ${message.role}`}>
                      <div className="message-avatar">{message.role === "user" ? setup?.user.display_name.slice(0, 1) : "ع"}</div>
                      <div className="message-body">
                        <div className="message-meta">
                          <strong>{message.role === "user" ? "أنت" : "عَوْن"}</strong>
                          <time>{formatTime(message.created_at)}</time>
                        </div>
                        <p>{messageText(message)}</p>
                      </div>
                    </article>
                  ))}
                  {runs[0]?.status === "received" && (
                    <div className="run-note">
                      <span className="pulse" />
                      تم استلام الطلب. سيبدأ التخطيط بعد إضافة منسق عَوْن في الخطوة التالية.
                    </div>
                  )}
                </div>

                <form className="composer" onSubmit={sendMessage}>
                  <textarea
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    rows={3}
                    maxLength={100_000}
                    placeholder="اكتب ما تريد من عَوْن إنجازه…"
                    aria-label="رسالتك إلى عَوْن"
                  />
                  <div className="composer-footer">
                    <span>مستوى التفويض: استشاري</span>
                    <button className="send-button" disabled={busy || !draft.trim()}>
                      {busy ? "جارٍ الحفظ…" : "إرسال إلى عَوْن"}
                      <span>←</span>
                    </button>
                  </div>
                </form>
              </>
            ) : (
              <div className="chat-empty full">
                <div className="brand-mark large">ع</div>
                <h2>ابدأ جلسة عمل جديدة</h2>
                <p>أنشئ محادثة من القائمة، ثم أرسل أول طلب إلى عَوْن.</p>
              </div>
            )}
          </section>
        </section>
      </main>
    </div>
  );
}
