"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";
import type {
  ApprovalRequest,
  CancellationRequestResult,
  Conversation,
  Message,
  PlanStep,
  Run,
  RunCancellation,
  RunStatus,
  SetupState,
  Task,
  ToolCall,
  Workspace,
} from "@/lib/types";

type ScreenState = "loading" | "setup" | "ready" | "offline";
type RunDetails = {
  planSteps: PlanStep[];
  approvals: ApprovalRequest[];
  toolCalls: ToolCall[];
};

const EMPTY_RUN_DETAILS: RunDetails = { planSteps: [], approvals: [], toolCalls: [] };

const RUN_LABELS: Record<RunStatus, string> = {
  received: "مستلم",
  planning: "يخطط",
  needs_clarification: "يحتاج توضيحاً",
  ready: "جاهز",
  awaiting_approval: "بانتظار الموافقة",
  executing: "قيد التنفيذ",
  cancellation_requested: "طُلب إلغاؤه",
  cancellation_uncertain: "توقفه غير مؤكد",
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

const RISK_LABELS: Record<PlanStep["risk"], string> = {
  low: "منخفض",
  medium: "متوسط",
  high: "مرتفع",
  critical: "حرج",
};

const APPROVAL_LABELS: Record<ApprovalRequest["status"], string> = {
  pending: "بانتظار قرارك",
  approved: "تمت الموافقة",
  rejected: "مرفوض",
  expired: "منتهي الصلاحية",
  invalidated: "أُبطل بعد تغيير الخطة",
  consumed: "استُخدمت الموافقة",
};

const TOOL_STATUS_LABELS: Record<ToolCall["status"], string> = {
  pending: "في طابور التنفيذ",
  executing: "قيد التنفيذ",
  succeeded: "نجح",
  failed: "فشل",
  cancelled: "أُلغي",
  outcome_unknown: "النتيجة غير مؤكدة",
};

const CANCELLATION_COPY: Record<
  RunCancellation["status"],
  { icon: string; title: string; meaning: string; next: string }
> = {
  accepted: {
    icon: "⏸",
    title: "قُبل طلب الإلغاء؛ يجري التحقق",
    meaning: "ثُبّت أمر الإلغاء، لكن توقف الأثر الجاري لم يُؤكد بعد.",
    next: "لا تبدأ تشغيلًا بديلًا حتى تظهر النتيجة.",
  },
  uncertain: {
    icon: "?",
    title: "تعذر تأكيد التوقف",
    meaning: "لا يكفي الدليل الحالي للجزم بوقوع الأثر أو توقفه.",
    next: "لا تعاود التنفيذ؛ راجع آخر دليل أو المورد.",
  },
  cancelled: {
    icon: "■",
    title: "أُلغي قبل الأثر",
    meaning: "ثبت أن الأثر لم يبدأ، ومُنعت المحاولات التالية.",
    next: "لا يلزم إجراء آخر.",
  },
  partially_succeeded: {
    icon: "◐",
    title: "وقع أثر جزئي وتوقف الباقي",
    meaning: "بعض الأثر مثبت، ومنع عَوْن ما لم يبدأ.",
    next: "راجع الأثر المثبت؛ لا يوجد تعويض تلقائي.",
  },
  completed: {
    icon: "↦",
    title: "اكتمل الأثر وكان الإلغاء متأخرًا",
    meaning: "وصل أمر الإلغاء بعد تجاوز العمل نقطة التحكم الآمنة.",
    next: "راجع النتيجة؛ التعويض غير متاح تلقائيًا.",
  },
  execution_failed: {
    icon: "×",
    title: "توقف التنفيذ بسبب فشل",
    meaning: "لا يوجد أثر ناجح مثبت، ولم يكن الإلغاء وحده سبب التوقف.",
    next: "راجع الخطأ قبل اتخاذ إجراء جديد.",
  },
};

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

async function optionalCancellation(path: string): Promise<RunCancellation | null> {
  try {
    return await apiRequest<RunCancellation>(path);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

function mergeCancellations(
  current: Record<string, RunCancellation | null>,
  entries: readonly (readonly [string, RunCancellation | null])[],
): Record<string, RunCancellation | null> {
  const next = { ...current };
  for (const [runId, incoming] of entries) {
    const existing = next[runId];
    if (incoming === null) {
      if (!(runId in next)) next[runId] = null;
      continue;
    }
    if (!existing || Date.parse(incoming.updated_at) >= Date.parse(existing.updated_at)) {
      next[runId] = incoming;
    }
  }
  return next;
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
  const [selectedRunId, setSelectedRunId] = useState("");
  const selectedRunIdRef = useRef("");
  const detailRequestVersionsRef = useRef<Record<string, number>>({});
  const cancellationIntentVersionRef = useRef(0);
  const viewContextRef = useRef({ workspaceId: "", conversationId: "", version: 0 });
  const [runDetails, setRunDetails] = useState<Record<string, RunDetails>>({});
  const [cancellations, setCancellations] = useState<Record<string, RunCancellation | null>>({});
  const [cancellationNotices, setCancellationNotices] = useState<Record<string, string>>({});
  const [cancellingRunIds, setCancellingRunIds] = useState<Set<string>>(() => new Set());
  const [tasks, setTasks] = useState<Task[]>([]);
  const [autonomyLevel, setAutonomyLevel] = useState<0 | 2>(0);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const beginViewContext = useCallback((nextWorkspaceId: string, nextConversationId: string) => {
    const version = viewContextRef.current.version + 1;
    viewContextRef.current = {
      workspaceId: nextWorkspaceId,
      conversationId: nextConversationId,
      version,
    };
    cancellationIntentVersionRef.current += 1;
    return version;
  }, []);

  const isCurrentViewContext = useCallback(
    (nextWorkspaceId: string, nextConversationId: string, version: number) => {
      const current = viewContextRef.current;
      return (
        current.workspaceId === nextWorkspaceId &&
        current.conversationId === nextConversationId &&
        current.version === version
      );
    },
    [],
  );

  const loadConversation = useCallback(async (nextWorkspaceId: string, nextConversationId: string) => {
    const context = viewContextRef.current;
    if (
      context.workspaceId !== nextWorkspaceId ||
      context.conversationId !== nextConversationId
    ) {
      return;
    }
    const contextVersion = context.version;
    if (!nextWorkspaceId || !nextConversationId) {
      setMessages([]);
      setRuns([]);
      setSelectedRunId("");
      selectedRunIdRef.current = "";
      setRunDetails({});
      setCancellations({});
      return;
    }
    const base = `workspaces/${nextWorkspaceId}/conversations/${nextConversationId}`;
    const [nextMessages, nextRuns, nextTasks] = await Promise.all([
      apiRequest<Message[]>(`${base}/messages`),
      apiRequest<Run[]>(`${base}/runs`),
      apiRequest<Task[]>(`workspaces/${nextWorkspaceId}/tasks`),
    ]);
    if (!isCurrentViewContext(nextWorkspaceId, nextConversationId, contextVersion)) return;
    setMessages(nextMessages);
    setRuns(nextRuns);
    setTasks(nextTasks);
    const selectedRun =
      nextRuns.find((run) => run.id === selectedRunIdRef.current) ?? nextRuns[0] ?? null;
    selectedRunIdRef.current = selectedRun?.id ?? "";
    setSelectedRunId(selectedRunIdRef.current);
    const watchedRunIds = new Set(
      nextRuns.filter((run) => !TERMINAL_RUNS.has(run.status)).map((run) => run.id),
    );
    if (selectedRun) watchedRunIds.add(selectedRun.id);
    const detailsPromise = selectedRun
      ? Promise.all([
          apiRequest<PlanStep[]>(`${base}/runs/${selectedRun.id}/steps`),
          apiRequest<ApprovalRequest[]>(`${base}/runs/${selectedRun.id}/approvals`),
          apiRequest<ToolCall[]>(`${base}/runs/${selectedRun.id}/tool-calls`),
        ])
      : Promise.resolve<[PlanStep[], ApprovalRequest[], ToolCall[]]>([[], [], []]);
    const detailRequestVersion = selectedRun
      ? (detailRequestVersionsRef.current[selectedRun.id] ?? 0) + 1
      : 0;
    if (selectedRun) detailRequestVersionsRef.current[selectedRun.id] = detailRequestVersion;
    const cancellationsPromise = Promise.all(
      [...watchedRunIds].map(async (runId) => [
        runId,
        await optionalCancellation(`${base}/runs/${runId}/cancellation`),
      ] as const),
    );
    const [[nextPlanSteps, nextApprovals, nextToolCalls], cancellationEntries] =
      await Promise.all([detailsPromise, cancellationsPromise]);
    if (!isCurrentViewContext(nextWorkspaceId, nextConversationId, contextVersion)) return;
    if (
      selectedRun &&
      detailRequestVersionsRef.current[selectedRun.id] === detailRequestVersion
    ) {
      setRunDetails((current) => ({
        ...current,
        [selectedRun.id]: {
          planSteps: nextPlanSteps,
          approvals: nextApprovals,
          toolCalls: nextToolCalls,
        },
      }));
    }
    setCancellations((current) => mergeCancellations(current, cancellationEntries));
  }, [isCurrentViewContext]);

  const loadWorkspace = useCallback(async (nextWorkspaceId: string) => {
    if (!nextWorkspaceId) return;
    const context = viewContextRef.current;
    if (context.workspaceId !== nextWorkspaceId || context.conversationId !== "") return;
    const contextVersion = context.version;
    const [nextConversations, nextTasks] = await Promise.all([
      apiRequest<Conversation[]>(`workspaces/${nextWorkspaceId}/conversations`),
      apiRequest<Task[]>(`workspaces/${nextWorkspaceId}/tasks`),
    ]);
    if (!isCurrentViewContext(nextWorkspaceId, "", contextVersion)) return;
    setConversations(nextConversations);
    setTasks(nextTasks);
    const nextConversationId = nextConversations[0]?.id ?? "";
    beginViewContext(nextWorkspaceId, nextConversationId);
    setConversationId(nextConversationId);
    if (nextConversationId) {
      await loadConversation(nextWorkspaceId, nextConversationId);
    } else {
      setMessages([]);
      setRuns([]);
      setSelectedRunId("");
      selectedRunIdRef.current = "";
      setRunDetails({});
      setCancellations({});
    }
  }, [beginViewContext, isCurrentViewContext, loadConversation]);

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
      beginViewContext(nextWorkspaceId, "");
      setWorkspaceId(nextWorkspaceId);
      await loadWorkspace(nextWorkspaceId);
      setScreen("ready");
    } catch (error) {
      setNotice(errorMessage(error));
      setScreen("offline");
    }
  }, [beginViewContext, loadWorkspace]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void initialize(), 0);
    return () => window.clearTimeout(timeout);
  }, [initialize]);

  useEffect(() => {
    if (!workspaceId || !conversationId) return;
    const interval = window.setInterval(() => {
      void loadConversation(workspaceId, conversationId).catch(() => undefined);
    }, 2_500);
    return () => window.clearInterval(interval);
  }, [conversationId, loadConversation, workspaceId]);

  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === conversationId) ?? null,
    [conversationId, conversations],
  );
  const activeRunItems = runs.filter((run) => !TERMINAL_RUNS.has(run.status));
  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? null;
  const selectedRunDetails = selectedRun
    ? runDetails[selectedRun.id] ?? EMPTY_RUN_DETAILS
    : EMPTY_RUN_DETAILS;
  const { planSteps, approvals, toolCalls } = selectedRunDetails;
  const selectedCancellation = selectedRun ? cancellations[selectedRun.id] ?? null : null;
  const hasEvidenceConflict =
    selectedCancellation?.events.some((event) => event.event_type === "evidence_conflict") ?? false;
  const cancellationCopy = selectedCancellation
    ? hasEvidenceConflict
      ? {
          icon: "⇄",
          title: "تعارض دليل النتيجة؛ المصالحة مطلوبة",
          meaning: "وصل دليلان متحققان على حقيقتين متعارضتين، فسحب عَوْن صفة اليقين.",
          next: "لا تعاود التنفيذ؛ قارن آخر دليلين وتوقيتهما.",
        }
      : CANCELLATION_COPY[selectedCancellation.status]
    : null;
  const recentCancellationEvents = selectedCancellation?.events.slice(-2) ?? [];
  const latestConflictEvent = selectedCancellation
    ? [...selectedCancellation.events]
        .reverse()
        .find((event) => event.event_type === "evidence_conflict") ?? null
    : null;
  const relatedConflictEvent =
    selectedCancellation && latestConflictEvent?.related_evidence_fingerprint
      ? selectedCancellation.events.find(
          (event) =>
            event.evidence_fingerprint === latestConflictEvent.related_evidence_fingerprint,
        ) ?? null
      : null;

  async function selectWorkspace(nextWorkspaceId: string) {
    const contextVersion = beginViewContext(nextWorkspaceId, "");
    setWorkspaceId(nextWorkspaceId);
    setConversationId("");
    setConversations([]);
    setMessages([]);
    setRuns([]);
    setSelectedRunId("");
    selectedRunIdRef.current = "";
    setRunDetails({});
    setCancellations({});
    setTasks([]);
    setNotice(null);
    try {
      await loadWorkspace(nextWorkspaceId);
    } catch (error) {
      if (isCurrentViewContext(nextWorkspaceId, "", contextVersion)) {
        setNotice(errorMessage(error));
      }
    }
  }

  async function selectConversation(nextConversationId: string) {
    const contextVersion = beginViewContext(workspaceId, nextConversationId);
    setConversationId(nextConversationId);
    setMessages([]);
    setRuns([]);
    setSelectedRunId("");
    selectedRunIdRef.current = "";
    setRunDetails({});
    setCancellations({});
    setNotice(null);
    try {
      await loadConversation(workspaceId, nextConversationId);
    } catch (error) {
      if (isCurrentViewContext(workspaceId, nextConversationId, contextVersion)) {
        setNotice(errorMessage(error));
      }
    }
  }

  async function selectRun(nextRunId: string) {
    if (!workspaceId || !conversationId) return;
    const contextVersion = viewContextRef.current.version;
    selectedRunIdRef.current = nextRunId;
    setSelectedRunId(nextRunId);
    const detailRequestVersion =
      (detailRequestVersionsRef.current[nextRunId] ?? 0) + 1;
    detailRequestVersionsRef.current[nextRunId] = detailRequestVersion;
    const base = `workspaces/${workspaceId}/conversations/${conversationId}/runs/${nextRunId}`;
    try {
      const [nextPlanSteps, nextApprovals, nextToolCalls, cancellation] = await Promise.all([
        apiRequest<PlanStep[]>(`${base}/steps`),
        apiRequest<ApprovalRequest[]>(`${base}/approvals`),
        apiRequest<ToolCall[]>(`${base}/tool-calls`),
        optionalCancellation(`${base}/cancellation`),
      ]);
      if (
        isCurrentViewContext(workspaceId, conversationId, contextVersion) &&
        detailRequestVersionsRef.current[nextRunId] === detailRequestVersion
      ) {
        setRunDetails((current) => ({
          ...current,
          [nextRunId]: {
            planSteps: nextPlanSteps,
            approvals: nextApprovals,
            toolCalls: nextToolCalls,
          },
        }));
      }
      if (isCurrentViewContext(workspaceId, conversationId, contextVersion)) {
        setCancellations((current) =>
          mergeCancellations(current, [[nextRunId, cancellation]]),
        );
      }
    } catch (error) {
      if (isCurrentViewContext(workspaceId, conversationId, contextVersion)) {
        setNotice(errorMessage(error));
      }
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
    const requestWorkspaceId = workspaceId;
    const requestContext = { ...viewContextRef.current };
    if (requestContext.workspaceId !== requestWorkspaceId) return;
    setBusy(true);
    setNotice(null);
    const form = event.currentTarget;
    const data = new FormData(form);
    let createdConversationId = "";
    let createdContextVersion: number | null = null;
    try {
      const conversation = await apiRequest<Conversation>(
        `workspaces/${requestWorkspaceId}/conversations`,
        {
          method: "POST",
          body: JSON.stringify({ title: data.get("title") }),
        },
      );
      if (
        !isCurrentViewContext(
          requestWorkspaceId,
          requestContext.conversationId,
          requestContext.version,
        )
      ) {
        return;
      }
      setConversations((current) => [conversation, ...current]);
      createdConversationId = conversation.id;
      createdContextVersion = beginViewContext(requestWorkspaceId, conversation.id);
      setConversationId(conversation.id);
      setMessages([]);
      setRuns([]);
      setSelectedRunId("");
      selectedRunIdRef.current = "";
      setRunDetails({});
      setCancellations({});
      form.reset();
      await loadConversation(requestWorkspaceId, conversation.id);
    } catch (error) {
      const errorBelongsToCurrentView =
        createdContextVersion === null
          ? isCurrentViewContext(
              requestWorkspaceId,
              requestContext.conversationId,
              requestContext.version,
            )
          : isCurrentViewContext(
              requestWorkspaceId,
              createdConversationId,
              createdContextVersion,
            );
      if (errorBelongsToCurrentView) setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!workspaceId || !conversationId || !text) return;
    const requestWorkspaceId = workspaceId;
    const requestConversationId = conversationId;
    const requestContextVersion = viewContextRef.current.version;
    if (
      !isCurrentViewContext(
        requestWorkspaceId,
        requestConversationId,
        requestContextVersion,
      )
    ) {
      return;
    }
    const isRequestContextCurrent = () =>
      isCurrentViewContext(
        requestWorkspaceId,
        requestConversationId,
        requestContextVersion,
      );
    const requestAutonomyLevel = autonomyLevel;
    setBusy(true);
    setNotice(null);
    setDraft("");
    const base = `workspaces/${requestWorkspaceId}/conversations/${requestConversationId}`;
    try {
      const message = await apiRequest<Message>(`${base}/messages`, {
        method: "POST",
        body: JSON.stringify({ parts: [{ type: "text", text }] }),
      });
      if (isRequestContextCurrent()) {
        setMessages((current) => [...current, message]);
      }

      const run = await apiRequest<Run>(`${base}/runs`, {
        method: "POST",
        body: JSON.stringify({
          request_message_id: message.id,
          autonomy_level: requestAutonomyLevel,
        }),
      });
      if (!isRequestContextCurrent()) return;
      setRuns((current) => [run, ...current]);
      selectedRunIdRef.current = run.id;
      setSelectedRunId(run.id);
      setRunDetails((current) => ({ ...current, [run.id]: EMPTY_RUN_DETAILS }));
      setCancellations((current) => ({ ...current, [run.id]: null }));
    } catch (error) {
      if (isRequestContextCurrent()) {
        setNotice(errorMessage(error));
        setDraft((current) => current || text);
        await loadConversation(requestWorkspaceId, requestConversationId).catch(() => undefined);
      }
    } finally {
      setBusy(false);
    }
  }

  async function decideApproval(
    approval: ApprovalRequest,
    decision: "approve" | "reject",
  ) {
    if (!workspaceId || !conversationId) return;
    const requestWorkspaceId = workspaceId;
    const requestConversationId = conversationId;
    const requestContextVersion = viewContextRef.current.version;
    const isRequestContextCurrent = () =>
      isCurrentViewContext(
        requestWorkspaceId,
        requestConversationId,
        requestContextVersion,
      );
    setBusy(true);
    setNotice(null);
    const path =
      `workspaces/${requestWorkspaceId}/conversations/${requestConversationId}` +
      `/runs/${approval.run_id}/approvals/${approval.id}/decision`;
    try {
      await apiRequest<ApprovalRequest>(path, {
        method: "POST",
        body: JSON.stringify({
          decision,
          action_fingerprint: approval.action_fingerprint,
        }),
      });
      await loadConversation(requestWorkspaceId, requestConversationId);
      if (isRequestContextCurrent()) {
        setNotice(
          decision === "approve"
            ? "سُجلت الموافقة وأُضيف الإجراء إلى التنفيذ؛ ستظهر النتيجة بعد تحقق العامل."
            : "رُفض الطلب وأُلغي هذا التشغيل.",
        );
      }
    } catch (error) {
      if (isRequestContextCurrent()) {
        setNotice(errorMessage(error));
        await loadConversation(requestWorkspaceId, requestConversationId).catch(() => undefined);
      }
    } finally {
      setBusy(false);
    }
  }

  async function requestCancellation(run: Run) {
    if (!workspaceId || !conversationId) return;
    if (run.workspace_id !== workspaceId || run.conversation_id !== conversationId) {
      setNotice("تعذر إلغاء تشغيل من خارج المحادثة الحالية. حدّث العرض وحاول مجددًا.");
      return;
    }
    const requestWorkspaceId = workspaceId;
    const requestConversationId = conversationId;
    const viewContextVersion = viewContextRef.current.version;
    const isRequestContextCurrent = () =>
      isCurrentViewContext(
        requestWorkspaceId,
        requestConversationId,
        viewContextVersion,
      );
    const intentVersion = cancellationIntentVersionRef.current + 1;
    cancellationIntentVersionRef.current = intentVersion;
    const path =
      `workspaces/${requestWorkspaceId}/conversations/${requestConversationId}` +
      `/runs/${run.id}/cancellation`;
    setCancellingRunIds((current) => new Set(current).add(run.id));
    setCancellationNotices((current) => {
      const next = { ...current };
      delete next[run.id];
      return next;
    });
    try {
      const result = await apiRequest<CancellationRequestResult>(path, { method: "POST" });
      if (!isRequestContextCurrent()) return;
      if (result.cancellation) {
        setCancellations((current) =>
          mergeCancellations(current, [[run.id, result.cancellation]]),
        );
      }
      if (result.decision === "too_late") {
        setCancellationNotices((current) => ({
          ...current,
          [run.id]: "وصل الطلب بعد اكتمال التشغيل؛ لم يتغير تاريخه.",
        }));
      } else if (result.decision === "not_cancellable") {
        setCancellationNotices((current) => ({
          ...current,
          [run.id]: "هذا التشغيل ليس في حالة تسمح بإلغاء التنفيذ.",
        }));
      }
      const isLatestIntent =
        isRequestContextCurrent() && cancellationIntentVersionRef.current === intentVersion;
      if (isLatestIntent) {
        selectedRunIdRef.current = run.id;
        setSelectedRunId(run.id);
      }
      await loadConversation(requestWorkspaceId, requestConversationId);
      if (!isRequestContextCurrent()) return;
      const feedbackId = result.cancellation
        ? `cancellation-${run.id}`
        : `cancellation-notice-${run.id}`;
      if (isLatestIntent) {
        window.setTimeout(() => {
          if (
            isRequestContextCurrent() &&
            cancellationIntentVersionRef.current === intentVersion
          ) {
            document.getElementById(feedbackId)?.focus();
          }
        }, 0);
      }
    } catch {
      if (!isRequestContextCurrent()) return;
      try {
        const recovered = await optionalCancellation(path);
        if (!isRequestContextCurrent()) return;
        if (recovered) {
          const isLatestIntent =
            isRequestContextCurrent() && cancellationIntentVersionRef.current === intentVersion;
          if (isLatestIntent) {
            selectedRunIdRef.current = run.id;
            setSelectedRunId(run.id);
          }
          setCancellations((current) =>
            mergeCancellations(current, [[run.id, recovered]]),
          );
          setCancellationNotices((current) => ({
            ...current,
            [run.id]: "تعذرت استجابة الطلب، لكن عَوْن أكد أن أمر الإلغاء محفوظ.",
          }));
          await loadConversation(requestWorkspaceId, requestConversationId);
          if (!isRequestContextCurrent()) return;
          if (isLatestIntent) {
            window.setTimeout(
              () => {
                if (
                  isRequestContextCurrent() &&
                  cancellationIntentVersionRef.current === intentVersion
                ) {
                  document.getElementById(`cancellation-${run.id}`)?.focus();
                }
              },
              0,
            );
          }
        } else {
          setCancellationNotices((current) => ({
            ...current,
            [run.id]: "لم يتأكد قبول الإلغاء. يمكنك إعادة المحاولة بأمان.",
          }));
          if (
            isRequestContextCurrent() &&
            cancellationIntentVersionRef.current === intentVersion
          ) {
            window.setTimeout(
              () => {
                if (isRequestContextCurrent()) {
                  document.getElementById(`cancellation-notice-${run.id}`)?.focus();
                }
              },
              0,
            );
          }
        }
      } catch {
        if (!isRequestContextCurrent()) return;
        setCancellationNotices((current) => ({
          ...current,
          [run.id]: "تعذر التحقق من قبول الإلغاء. إعادة الطلب آمنة ولا تكرر الأثر.",
        }));
        if (
          isRequestContextCurrent() &&
          cancellationIntentVersionRef.current === intentVersion
        ) {
          window.setTimeout(
            () => {
              if (isRequestContextCurrent()) {
                document.getElementById(`cancellation-notice-${run.id}`)?.focus();
              }
            },
            0,
          );
        }
      }
    } finally {
      setCancellingRunIds((current) => {
        const next = new Set(current);
        next.delete(run.id);
        return next;
      });
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
          <button
            className="nav-item"
            type="button"
            onClick={() => document.getElementById("tasks")?.scrollIntoView({ behavior: "smooth" })}
          >
            <span>✓</span>المهام
          </button>
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
            <span className="stat-label">المهام</span>
            <strong>{tasks.length}</strong>
            <small>في المساحة الحالية</small>
          </article>
          <article className="stat-card">
            <span className="stat-label">التشغيلات النشطة</span>
            <strong>{activeRunItems.length}</strong>
            <small>{activeRunItems.length ? "تُحدّث تلقائيًا" : "لا يوجد عمل معلّق"}</small>
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
                  {selectedRun && (
                    <span className={`run-badge status-${selectedRun.status}`}>
                      {RUN_LABELS[selectedRun.status]}
                    </span>
                  )}
                </header>

                {runs.length > 0 && (
                  <section className="active-runs-panel" aria-label="تشغيلات المحادثة">
                    <header>
                      <div>
                        <span className="eyebrow">التشغيلات</span>
                        <h3>اختر التشغيل الذي تريد متابعته</h3>
                      </div>
                      <span className="count-badge">{runs.length}</span>
                    </header>
                    <div className="run-list">
                      {runs.map((run) => {
                        const cancellation = cancellations[run.id];
                        const shortId = run.id.slice(0, 8);
                        return (
                          <article
                            className={`run-row ${run.id === selectedRunId ? "selected" : ""}`}
                            key={run.id}
                          >
                            <button
                              type="button"
                              className="run-select"
                              aria-current={run.id === selectedRunId ? "true" : undefined}
                              onClick={() => void selectRun(run.id)}
                            >
                              <span className="run-identity">
                                <strong>تشغيل <bdi dir="ltr">{shortId}</bdi></strong>
                                <small>{formatTime(run.created_at)}</small>
                              </span>
                              <span className={`run-badge status-${run.status}`}>
                                {RUN_LABELS[run.status]}
                              </span>
                              {cancellation && (
                                <small className="run-cancellation-state">
                                  {CANCELLATION_COPY[cancellation.status].icon}{" "}
                                  {CANCELLATION_COPY[cancellation.status].title}
                                </small>
                              )}
                            </button>
                            {run.status === "executing" && !cancellation && (
                              <button
                                id={`cancel-button-${run.id}`}
                                type="button"
                                className="cancel-run-button"
                                disabled={cancellingRunIds.has(run.id)}
                                aria-label={`إلغاء التنفيذ ${shortId} عند ${formatTime(run.created_at)}`}
                                onClick={() => void requestCancellation(run)}
                              >
                                {cancellingRunIds.has(run.id)
                                  ? "جارٍ إرسال الطلب…"
                                  : "إلغاء التنفيذ"}
                              </button>
                            )}
                            {cancellationNotices[run.id] && (
                              <p
                                id={`cancellation-notice-${run.id}`}
                                className="run-cancellation-notice"
                                role="status"
                                tabIndex={-1}
                              >
                                {cancellationNotices[run.id]}
                              </p>
                            )}
                          </article>
                        );
                      })}
                    </div>
                  </section>
                )}

                {selectedCancellation && cancellationCopy && selectedRun && (
                  <section
                    id={`cancellation-${selectedRun.id}`}
                    className={`cancellation-card cancellation-${selectedCancellation.status} ${hasEvidenceConflict ? "cancellation-conflict" : ""}`}
                    role="status"
                    aria-live="polite"
                    aria-label={`حالة إلغاء التشغيل ${selectedRun.id.slice(0, 8)}`}
                    tabIndex={-1}
                  >
                    <header>
                      <span className="cancellation-icon" aria-hidden="true">
                        {cancellationCopy.icon}
                      </span>
                      <div>
                        <span className="eyebrow">فرامل التشغيل</span>
                        <h3>{cancellationCopy.title}</h3>
                      </div>
                    </header>
                    <p>{cancellationCopy.meaning}</p>
                    <div className="cancellation-next">
                      <strong>الخطوة الآمنة التالية</strong>
                      <span>{cancellationCopy.next}</span>
                    </div>
                    <ol className="cancellation-timeline" aria-label="خط الإلغاء الزمني">
                      <li>
                        <span>↓</span>
                        <div>
                          <strong>استلم عَوْن الطلب</strong>
                          <time>{formatTime(selectedCancellation.received_at)}</time>
                        </div>
                      </li>
                      <li>
                        <span>⏸</span>
                        <div>
                          <strong>ثُبّت أمر الإلغاء</strong>
                          <time>{formatTime(selectedCancellation.requested_at)}</time>
                        </div>
                      </li>
                    </ol>
                    {recentCancellationEvents.length > 0 && (
                      <details className="cancellation-evidence" open={hasEvidenceConflict}>
                        <summary>آخر دليل مسجل</summary>
                        <ul>
                          {recentCancellationEvents.map((event) => (
                            <li key={event.id}>
                              <bdi dir="ltr">{event.evidence_code}</bdi>
                              <time>{formatTime(event.observed_at)}</time>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                    {latestConflictEvent && (
                      <dl className="cancellation-conflict-evidence">
                        <div>
                          <dt>الحالة السابقة التي سُحب يقينها</dt>
                          <dd>
                            <bdi dir="ltr">
                              {latestConflictEvent.superseded_status ?? "غير مسجلة"}
                            </bdi>
                          </dd>
                        </div>
                        <div>
                          <dt>الدليل الأحدث</dt>
                          <dd>
                            <bdi dir="ltr">
                              {latestConflictEvent.evidence_fingerprint ?? "غير متاح"}
                            </bdi>
                            <time>{formatTime(latestConflictEvent.observed_at)}</time>
                          </dd>
                        </div>
                        <div>
                          <dt>الدليل المتعارض معه</dt>
                          <dd>
                            <bdi dir="ltr">
                              {latestConflictEvent.related_evidence_fingerprint ?? "غير متاح"}
                            </bdi>
                            {relatedConflictEvent && (
                              <time>{formatTime(relatedConflictEvent.observed_at)}</time>
                            )}
                          </dd>
                        </div>
                      </dl>
                    )}
                  </section>
                )}

                <div className="messages">
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
                  {selectedRun?.status === "received" && (
                    <div className="run-note">
                      <span className="pulse" />
                      تم استلام الطلب، وسيبدأ عَوْن بتحليله الآن.
                    </div>
                  )}
                  {selectedRun?.status === "planning" && (
                    <div className="run-note">
                      <span className="pulse" />
                      يجري إعداد إجابة أو خطة منظمة قابلة للمراجعة.
                    </div>
                  )}
                  {planSteps.length > 0 && (
                    <section className="plan-card" aria-label="الخطة المقترحة">
                      <header>
                        <div>
                          <span className="eyebrow">الخطة المقترحة</span>
                          <h3>خطوات العمل قبل التنفيذ</h3>
                        </div>
                        <span className="count-badge">{planSteps.length}</span>
                      </header>
                      <ol>
                        {planSteps.map((step) => (
                          <li key={step.id}>
                            <span className="step-number">{step.position + 1}</span>
                            <span className="step-copy">
                              <strong>{step.title}</strong>
                              <small>
                                المخاطر: {RISK_LABELS[step.risk]}
                                {step.requires_approval ? " · يحتاج موافقة" : " · لا يحتاج موافقة"}
                              </small>
                              {step.tool_name && (
                                <code className="tool-action" dir="ltr">
                                  {step.tool_name}.{step.operation} {JSON.stringify(step.tool_input)}
                                </code>
                              )}
                            </span>
                          </li>
                        ))}
                      </ol>
                      <p>هذه خطة فقط؛ لم ينفذ عَوْن أي إجراء بعد.</p>
                    </section>
                  )}
                  {approvals[0] && (
                    <section
                      className={`approval-card approval-${approvals[0].status}`}
                      aria-label="طلب الموافقة"
                    >
                      <header>
                        <div>
                          <span className="eyebrow">طلب موافقة</span>
                          <h3>{APPROVAL_LABELS[approvals[0].status]}</h3>
                        </div>
                        <span className={`risk-badge risk-${approvals[0].risk}`}>
                          مخاطر {RISK_LABELS[approvals[0].risk]}
                        </span>
                      </header>
                      <p>{approvals[0].summary}</p>
                      <div className="fingerprint-row">
                        <span>بصمة الإجراء</span>
                        <code dir="ltr">{approvals[0].action_fingerprint}</code>
                      </div>
                      {approvals[0].status === "pending" ? (
                        <>
                          <small>
                            تنتهي صلاحية الطلب عند {formatTime(approvals[0].expires_at)}.
                            الموافقة مرتبطة بهذه الخطة والبصمة فقط.
                          </small>
                          <div className="approval-actions">
                            <button
                              className="reject-button"
                              type="button"
                              disabled={busy}
                              onClick={() => void decideApproval(approvals[0], "reject")}
                            >
                              رفض وإلغاء التشغيل
                            </button>
                            <button
                              className="primary-button"
                              type="button"
                              disabled={busy}
                              onClick={() => void decideApproval(approvals[0], "approve")}
                            >
                              مراجعة واعتماد الخطة
                            </button>
                          </div>
                        </>
                      ) : (
                        <small>
                          {approvals[0].status === "approved"
                            ? "الموافقة مسجلة، ويجري تنفيذ الإجراء المصرح به."
                            : approvals[0].status === "consumed"
                              ? "استُهلكت هذه الموافقة في التنفيذ المسجل أعلاه، ولا يمكن إعادة استخدامها لأثر جديد."
                              : "لن يستخدم عَوْن هذا الطلب في أي تنفيذ."}
                        </small>
                      )}
                    </section>
                  )}
                  {toolCalls.map((call) => (
                    <section className={`tool-call-card tool-${call.status}`} key={call.id}>
                      <header>
                        <div>
                          <span className="eyebrow">سجل الأداة</span>
                          <h3 dir="ltr">{call.tool_name}.{call.operation}</h3>
                        </div>
                        <span className="count-badge">{TOOL_STATUS_LABELS[call.status]}</span>
                      </header>
                      <p>
                        {call.status === "succeeded"
                          ? "اكتمل التنفيذ وحُفظت النتيجة بنجاح."
                          : call.status === "failed"
                            ? "فشل التنفيذ بعد المحاولات المسموحة، وحُفظ رمز الخطأ دون ادعاء النجاح."
                            : call.status === "pending"
                              ? "حُفظ الإجراء في الطابور الدائم وينتظر العامل أو موعد إعادة المحاولة."
                               : call.status === "cancelled"
                                  ? "أُلغي هذا الإجراء قبل تنفيذه."
                                  : call.status === "outcome_unknown"
                                    ? "تعذر تأكيد أثر هذا الاستدعاء؛ لن يعيد عَوْن تشغيله تلقائيًا."
                                    : "حصل العامل على مهلة التنفيذ ويجري تشغيل الإجراء المصرح به."}
                      </p>
                      {typeof call.output?.path === "string" && (
                        <p>
                          الملف: <code dir="ltr">{call.output.path}</code>
                        </p>
                      )}
                      <small>
                        المحاولة {call.attempt_count} من {call.max_attempts}
                      </small>
                      <code dir="ltr">{call.idempotency_key}</code>
                    </section>
                  ))}
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
                    <label className="autonomy-picker">
                      <span>مستوى التفويض</span>
                      <select
                        value={autonomyLevel}
                        onChange={(event) => setAutonomyLevel(Number(event.target.value) as 0 | 2)}
                        aria-label="مستوى التفويض"
                      >
                        <option value={0}>استشاري — خطة فقط</option>
                        <option value={2}>منفّذ بإذن — موافقة قبل الأثر</option>
                      </select>
                    </label>
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

        <section className="tasks-board" id="tasks" aria-label="مهام مساحة العمل">
          <header>
            <div>
              <span className="eyebrow">المهام</span>
              <h2>قائمة العمل الحالية</h2>
            </div>
            <span className="count-badge">{tasks.length}</span>
          </header>
          {tasks.length === 0 ? (
            <p className="tasks-empty">لا توجد مهام بعد. اطلب من عَوْن إنشاء مهمة واختر «منفّذ بإذن».</p>
          ) : (
            <div className="task-list">
              {tasks.map((task) => (
                <article className="task-row" key={task.id}>
                  <span className={`task-state task-${task.status}`} />
                  <div>
                    <strong>{task.title}</strong>
                    <small>{task.status} · أولوية {task.priority}</small>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
