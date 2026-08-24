import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "@/lib/api";
import type {
  CancellationRequestResult,
  Conversation,
  Message,
  Run,
  RunCancellation,
  ToolCall,
} from "@/lib/types";

import { Dashboard } from "./dashboard";

const dashboardStyles = readFileSync(
  resolve(process.cwd(), "src/app/globals.css"),
  "utf8",
);

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiRequest: vi.fn() };
});

const mockedApi = vi.mocked(apiRequest);
const workspaceId = "11111111-1111-4111-8111-111111111111";
const conversationId = "22222222-2222-4222-8222-222222222222";
const conversationBId = "22222222-2222-4222-8222-222222222223";
const newConversationId = "22222222-2222-4222-8222-222222222224";
const runAId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const runBId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const now = "2026-08-24T08:00:00.000Z";
let dashboardStyleElement: HTMLStyleElement;

beforeAll(() => {
  dashboardStyleElement = document.createElement("style");
  dashboardStyleElement.textContent = dashboardStyles;
  document.head.appendChild(dashboardStyleElement);
});

afterAll(() => dashboardStyleElement.remove());

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function run(
  id: string,
  status: Run["status"] = "executing",
  ownerConversationId = conversationId,
): Run {
  return {
    id,
    workspace_id: workspaceId,
    conversation_id: ownerConversationId,
    request_message_id: null,
    trace_id: id,
    status,
    risk: "low",
    autonomy_level: 2,
    error_code: null,
    started_at: now,
    completed_at: null,
    created_at: now,
    updated_at: now,
  };
}

function call(runId: string, toolName: string): ToolCall {
  return {
    id: `${runId.slice(0, 8)}-0000-4000-8000-000000000000`,
    run_id: runId,
    plan_step_id: `${runId.slice(0, 8)}-0001-4000-8000-000000000000`,
    tool_name: toolName,
    operation: "create",
    input: { title: toolName },
    output: null,
    status: "executing",
    risk: "low",
    idempotency_key: toolName.repeat(64).slice(0, 64),
    error_code: null,
    attempt_count: 1,
    max_attempts: 3,
    available_at: now,
    lease_expires_at: now,
    started_at: now,
    effect_committed_at: null,
    completed_at: null,
    created_at: now,
    updated_at: now,
  };
}

function cancellation(
  runId: string,
  status: RunCancellation["status"] = "accepted",
): RunCancellation {
  return {
    id: `${runId.slice(0, 8)}-0002-4000-8000-000000000000`,
    run_id: runId,
    requested_by: "33333333-3333-4333-8333-333333333333",
    status,
    reason_code: "OWNER_REQUEST",
    received_at: now,
    requested_at: now,
    resolved_at: status === "accepted" ? null : now,
    updated_at: now,
    events: [
      {
        id: `${runId.slice(0, 8)}-0003-4000-8000-000000000000`,
        cancellation_id: `${runId.slice(0, 8)}-0002-4000-8000-000000000000`,
        sequence_no: 1,
        tool_call_id: null,
        event_type: "request_accepted",
        source_type: "owner_action",
        evidence_code: "OWNER_REQUEST_COMMITTED",
        evidence_fingerprint: null,
        related_evidence_fingerprint: null,
        superseded_status: null,
        occurred_at: now,
        observed_at: now,
      },
    ],
  };
}

function installApi(
  runs: Run[],
  options: {
    cancellationByRun?: Record<string, RunCancellation | null>;
    postCancellation?: (runId: string) => Promise<CancellationRequestResult>;
    detailGateByRun?: Record<string, Promise<void>>;
    conversations?: Conversation[];
    runsByConversation?: Record<string, Run[]>;
    messagesByConversation?: Record<string, Message[]>;
    postConversation?: () => Promise<Conversation>;
    postMessage?: (activeConversationId: string) => Promise<Message>;
    postRun?: (activeConversationId: string, requestMessageId: string) => Promise<Run>;
  } = {},
) {
  const cancellationByRun = options.cancellationByRun ?? {};
  const availableConversations = options.conversations ?? [
    {
      id: conversationId,
      workspace_id: workspaceId,
      title: "اختبار فرامل التشغيل",
      status: "active",
      summary: null,
      created_at: now,
      updated_at: now,
    },
  ];
  mockedApi.mockImplementation(async (path, init) => {
    if (path === "ready") return { status: "ready" } as never;
    if (path === "setup") {
      return {
        user: {
          id: "33333333-3333-4333-8333-333333333333",
          display_name: "المالك",
          locale: "ar",
          timezone: "Asia/Dubai",
          created_at: now,
          updated_at: now,
        },
        workspace: {
          id: workspaceId,
          owner_id: "33333333-3333-4333-8333-333333333333",
          name: "مساحة الاختبار",
          status: "active",
          created_at: now,
          updated_at: now,
        },
        created: false,
      } as never;
    }
    if (path === "workspaces") {
      return [
        {
          id: workspaceId,
          owner_id: "33333333-3333-4333-8333-333333333333",
          name: "مساحة الاختبار",
          status: "active",
          created_at: now,
          updated_at: now,
        },
      ] as never;
    }
    if (
      path === `workspaces/${workspaceId}/conversations` &&
      init?.method === "POST"
    ) {
      if (!options.postConversation) throw new Error("unexpected conversation POST");
      const conversation = await options.postConversation();
      if (!availableConversations.some((item) => item.id === conversation.id)) {
        availableConversations.unshift(conversation);
      }
      return conversation as never;
    }
    if (path === `workspaces/${workspaceId}/conversations`) {
      return availableConversations as never;
    }
    if (path === `workspaces/${workspaceId}/tasks`) return [] as never;

    const activeConversation = availableConversations.find((conversation) =>
      path.startsWith(
        `workspaces/${workspaceId}/conversations/${conversation.id}`,
      ),
    );
    const activeBase = activeConversation
      ? `workspaces/${workspaceId}/conversations/${activeConversation.id}`
      : null;
    const activeRuns = activeConversation
      ? options.runsByConversation?.[activeConversation.id] ?? runs
      : [];
    const activeMessages = activeConversation
      ? options.messagesByConversation?.[activeConversation.id] ?? []
      : [];
    if (activeConversation && activeBase && path === `${activeBase}/messages`) {
      if (init?.method === "POST") {
        if (!options.postMessage) throw new Error("unexpected message POST");
        const message = await options.postMessage(activeConversation.id);
        if (!activeMessages.some((item) => item.id === message.id)) activeMessages.push(message);
        return message as never;
      }
      return [...activeMessages] as never;
    }
    if (activeConversation && activeBase && path === `${activeBase}/runs`) {
      if (init?.method === "POST") {
        if (!options.postRun) throw new Error("unexpected run POST");
        const body = JSON.parse(String(init.body)) as { request_message_id: string };
        const createdRun = await options.postRun(
          activeConversation.id,
          body.request_message_id,
        );
        if (!activeRuns.some((item) => item.id === createdRun.id)) activeRuns.unshift(createdRun);
        return createdRun as never;
      }
      return [...activeRuns] as never;
    }

    const matchedRun = activeRuns.find((item) => path.includes(`/runs/${item.id}/`));
    if (matchedRun) {
      if (init?.method === "POST" && path.endsWith("/cancellation")) {
        if (!options.postCancellation) throw new Error("unexpected cancellation POST");
        return (await options.postCancellation(matchedRun.id)) as never;
      }
      const gate = options.detailGateByRun?.[matchedRun.id];
      if (gate && !path.endsWith("/cancellation")) await gate;
      if (path.endsWith("/steps")) return [] as never;
      if (path.endsWith("/approvals")) return [] as never;
      if (path.endsWith("/tool-calls")) {
        return [call(matchedRun.id, matchedRun.id === runAId ? "alpha" : "beta")] as never;
      }
      if (path.endsWith("/cancellation")) {
        const value = cancellationByRun[matchedRun.id];
        if (!value) throw new ApiError(404, "not found");
        return value as never;
      }
    }
    throw new Error(`Unhandled API path: ${path}`);
  });
}

afterEach(() => {
  mockedApi.mockReset();
});

describe("FC-002 cancellation dashboard", () => {
  it("keeps details keyed to the selected run when responses arrive in reverse order", async () => {
    const gateB = deferred<void>();
    installApi([run(runAId), run(runBId)], {
      detailGateByRun: { [runBId]: gateB.promise },
    });
    const user = userEvent.setup();
    render(<Dashboard />);

    expect(await screen.findByRole("heading", { name: "alpha.create" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /تشغيل bbbbbbbb/ }));
    await user.click(screen.getByRole("button", { name: /تشغيل aaaaaaaa/ }));
    expect(await screen.findByRole("heading", { name: "alpha.create" })).toBeTruthy();

    await act(async () => gateB.resolve());
    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "beta.create" })).toBeNull();
      expect(screen.getByRole("heading", { name: "alpha.create" })).toBeTruthy();
    });
  });

  it("tracks overlapping cancellation requests independently", async () => {
    const postA = deferred<CancellationRequestResult>();
    const postB = deferred<CancellationRequestResult>();
    const byRun: Record<string, RunCancellation | null> = {};
    installApi([run(runAId), run(runBId)], {
      cancellationByRun: byRun,
      postCancellation: (runId) => (runId === runAId ? postA.promise : postB.promise),
    });
    render(<Dashboard />);
    await screen.findByRole("heading", { name: "alpha.create" });

    const buttonA = document.getElementById(`cancel-button-${runAId}`) as HTMLButtonElement;
    const buttonB = document.getElementById(`cancel-button-${runBId}`) as HTMLButtonElement;
    fireEvent.click(buttonA);
    fireEvent.click(buttonB);
    await waitFor(() => {
      expect(buttonA.disabled).toBe(true);
      expect(buttonB.disabled).toBe(true);
    });

    const acceptedB = cancellation(runBId);
    byRun[runBId] = acceptedB;
    await act(async () => {
      postB.resolve({
        decision: "accepted",
        received_at: now,
        run_status: "cancellation_requested",
        cancellation: acceptedB,
      });
    });
    const cardB = await screen.findByLabelText("حالة إلغاء التشغيل bbbbbbbb");
    await waitFor(() => expect(document.activeElement).toBe(cardB));
    expect(buttonA.disabled).toBe(true);

    const acceptedA = cancellation(runAId);
    byRun[runAId] = acceptedA;
    await act(async () => {
      postA.resolve({
        decision: "accepted",
        received_at: now,
        run_status: "cancellation_requested",
        cancellation: acceptedA,
      });
    });
    await waitFor(() => {
      expect(screen.getByLabelText("حالة إلغاء التشغيل bbbbbbbb")).toBeTruthy();
      expect(screen.getByRole("heading", { name: "beta.create" })).toBeTruthy();
    });
  });

  it("ignores an old cancellation response after navigating to another conversation", async () => {
    const postA = deferred<CancellationRequestResult>();
    const byRun: Record<string, RunCancellation | null> = {};
    const conversations: Conversation[] = [
      {
        id: conversationId,
        workspace_id: workspaceId,
        title: "المحادثة أ",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      },
      {
        id: conversationBId,
        workspace_id: workspaceId,
        title: "المحادثة ب",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      },
    ];
    installApi([run(runAId)], {
      conversations,
      runsByConversation: {
        [conversationId]: [run(runAId)],
        [conversationBId]: [run(runBId)],
      },
      cancellationByRun: byRun,
      postCancellation: () => postA.promise,
    });
    const user = userEvent.setup();
    render(<Dashboard />);
    await screen.findByRole("heading", { name: "alpha.create" });

    fireEvent.click(document.getElementById(`cancel-button-${runAId}`)!);
    await user.click(screen.getByRole("button", { name: /المحادثة ب/ }));
    expect(await screen.findByRole("heading", { name: "beta.create" })).toBeTruthy();

    const acceptedA = cancellation(runAId);
    byRun[runAId] = acceptedA;
    await act(async () => {
      postA.resolve({
        decision: "accepted",
        received_at: now,
        run_status: "cancellation_requested",
        cancellation: acceptedA,
      });
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "beta.create" })).toBeTruthy();
      expect(screen.queryByLabelText("حالة إلغاء التشغيل aaaaaaaa")).toBeNull();
    });

    await user.click(screen.getByRole("button", { name: /المحادثة أ/ }));
    expect(await screen.findByLabelText("حالة إلغاء التشغيل aaaaaaaa")).toBeTruthy();
  });

  it("loads and controls runs after creating a conversation", async () => {
    const createdConversation: Conversation = {
      id: newConversationId,
      workspace_id: workspaceId,
      title: "محادثة جديدة",
      status: "active",
      summary: null,
      created_at: now,
      updated_at: now,
    };
    const accepted = cancellation(runBId);
    installApi([], {
      runsByConversation: {
        [conversationId]: [],
        [newConversationId]: [run(runBId, "executing", newConversationId)],
      },
      cancellationByRun: { [runBId]: null },
      postConversation: async () => createdConversation,
      postCancellation: async () => ({
        decision: "accepted",
        received_at: now,
        run_status: "cancellation_requested",
        cancellation: accepted,
      }),
    });
    const user = userEvent.setup();
    render(<Dashboard />);
    await screen.findByRole("heading", { name: "اختبار فرامل التشغيل" });

    await user.type(screen.getByPlaceholderText("عنوان محادثة جديدة"), "محادثة جديدة");
    await user.click(screen.getByRole("button", { name: "إنشاء محادثة" }));

    expect(await screen.findByRole("heading", { name: "beta.create" })).toBeTruthy();
    fireEvent.click(document.getElementById(`cancel-button-${runBId}`)!);
    expect(await screen.findByLabelText("حالة إلغاء التشغيل bbbbbbbb")).toBeTruthy();
  });

  it("does not let a delayed conversation creation steal a newer view", async () => {
    const postConversation = deferred<Conversation>();
    const conversations: Conversation[] = [
      {
        id: conversationId,
        workspace_id: workspaceId,
        title: "المحادثة أ",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      },
      {
        id: conversationBId,
        workspace_id: workspaceId,
        title: "المحادثة ب",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      },
    ];
    installApi([run(runAId)], {
      conversations,
      runsByConversation: {
        [conversationId]: [run(runAId)],
        [conversationBId]: [run(runBId, "executing", conversationBId)],
      },
      postConversation: () => postConversation.promise,
    });
    const user = userEvent.setup();
    render(<Dashboard />);
    await screen.findByRole("heading", { name: "alpha.create" });

    await user.type(screen.getByPlaceholderText("عنوان محادثة جديدة"), "طلب متأخر");
    fireEvent.click(screen.getByRole("button", { name: "إنشاء محادثة" }));
    await user.click(screen.getByRole("button", { name: /المحادثة ب/ }));
    expect(await screen.findByRole("heading", { name: "beta.create" })).toBeTruthy();

    await act(async () => {
      postConversation.resolve({
        id: newConversationId,
        workspace_id: workspaceId,
        title: "طلب متأخر",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      });
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "beta.create" })).toBeTruthy();
      expect(screen.queryByRole("heading", { name: "طلب متأخر" })).toBeNull();
    });
  });

  it("keeps a delayed message response in its originating conversation", async () => {
    const postMessage = deferred<Message>();
    const messagesByConversation: Record<string, Message[]> = {
      [conversationId]: [],
      [conversationBId]: [],
    };
    const runsByConversation: Record<string, Run[]> = {
      [conversationId]: [],
      [conversationBId]: [run(runBId, "executing", conversationBId)],
    };
    const conversations: Conversation[] = [
      {
        id: conversationId,
        workspace_id: workspaceId,
        title: "المحادثة أ",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      },
      {
        id: conversationBId,
        workspace_id: workspaceId,
        title: "المحادثة ب",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      },
    ];
    installApi([], {
      conversations,
      messagesByConversation,
      runsByConversation,
      postMessage: () => postMessage.promise,
      postRun: async (activeConversationId, requestMessageId) => ({
        ...run(runAId, "executing", activeConversationId),
        request_message_id: requestMessageId,
      }),
    });
    const user = userEvent.setup();
    render(<Dashboard />);
    await screen.findByRole("heading", { name: "المحادثة أ" });

    await user.type(screen.getByPlaceholderText("اكتب ما تريد من عَوْن إنجازه…"), "طلب المحادثة أ");
    fireEvent.click(screen.getByRole("button", { name: /إرسال إلى عَوْن/ }));
    await user.click(screen.getByRole("button", { name: /المحادثة ب/ }));
    expect(await screen.findByRole("heading", { name: "beta.create" })).toBeTruthy();

    await act(async () => {
      postMessage.resolve({
        id: "66666666-6666-4666-8666-666666666666",
        conversation_id: conversationId,
        role: "user",
        parts: [{ type: "text", text: "طلب المحادثة أ", data: null }],
        created_at: now,
      });
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "beta.create" })).toBeTruthy();
      expect(screen.queryByText("طلب المحادثة أ")).toBeNull();
    });

    await user.click(screen.getByRole("button", { name: /المحادثة أ/ }));
    expect(await screen.findByText("طلب المحادثة أ")).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "alpha.create" })).toBeTruthy();
  });

  it("keeps a delayed run response out of a newer conversation", async () => {
    const postRun = deferred<Run>();
    const messagesByConversation: Record<string, Message[]> = {
      [conversationId]: [],
      [conversationBId]: [],
    };
    const runsByConversation: Record<string, Run[]> = {
      [conversationId]: [],
      [conversationBId]: [run(runBId, "executing", conversationBId)],
    };
    const conversations: Conversation[] = [
      {
        id: conversationId,
        workspace_id: workspaceId,
        title: "المحادثة أ",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      },
      {
        id: conversationBId,
        workspace_id: workspaceId,
        title: "المحادثة ب",
        status: "active",
        summary: null,
        created_at: now,
        updated_at: now,
      },
    ];
    installApi([], {
      conversations,
      messagesByConversation,
      runsByConversation,
      postMessage: async (activeConversationId) => ({
        id: "77777777-7777-4777-8777-777777777777",
        conversation_id: activeConversationId,
        role: "user",
        parts: [{ type: "text", text: "تشغيل متأخر", data: null }],
        created_at: now,
      }),
      postRun: () => postRun.promise,
    });
    const user = userEvent.setup();
    render(<Dashboard />);
    await screen.findByRole("heading", { name: "المحادثة أ" });

    await user.type(screen.getByPlaceholderText("اكتب ما تريد من عَوْن إنجازه…"), "تشغيل متأخر");
    fireEvent.click(screen.getByRole("button", { name: /إرسال إلى عَوْن/ }));
    await screen.findByText("تشغيل متأخر");
    await user.click(screen.getByRole("button", { name: /المحادثة ب/ }));
    expect(await screen.findByRole("heading", { name: "beta.create" })).toBeTruthy();

    await act(async () => {
      const createdRun = run(runAId, "executing", conversationId);
      runsByConversation[conversationId].unshift(createdRun);
      postRun.resolve(createdRun);
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "beta.create" })).toBeTruthy();
      expect(screen.queryByRole("heading", { name: "alpha.create" })).toBeNull();
    });

    await user.click(screen.getByRole("button", { name: /المحادثة أ/ }));
    expect(await screen.findByRole("heading", { name: "alpha.create" })).toBeTruthy();
  });

  it("selects and focuses the recovered cancellation after a lost POST response", async () => {
    let recovered = false;
    const recoveredCancellation = cancellation(runBId);
    installApi([run(runAId), run(runBId)], {
      cancellationByRun: {
        get [runBId]() {
          return recovered ? recoveredCancellation : null;
        },
      },
      postCancellation: async () => {
        recovered = true;
        throw new Error("response lost");
      },
    });
    render(<Dashboard />);
    await screen.findByRole("heading", { name: "alpha.create" });

    fireEvent.click(document.getElementById(`cancel-button-${runBId}`)!);
    const card = await screen.findByLabelText("حالة إلغاء التشغيل bbbbbbbb");
    await waitFor(() => expect(document.activeElement).toBe(card));
    expect(screen.getByRole("heading", { name: "beta.create" })).toBeTruthy();
  });

  it("shows both evidence sides, their times, and the superseded status", async () => {
    const conflict = cancellation(runAId, "uncertain");
    const firstFingerprint = "a".repeat(64);
    const secondFingerprint = "b".repeat(64);
    conflict.events.push(
      {
        ...conflict.events[0],
        id: "44444444-4444-4444-8444-444444444444",
        sequence_no: 2,
        event_type: "late_effect_evidence",
        source_type: "current_worker",
        evidence_code: "VALIDATED_TOOL_OUTPUT",
        evidence_fingerprint: firstFingerprint,
        observed_at: "2026-08-24T08:01:00.000Z",
      },
      {
        ...conflict.events[0],
        id: "55555555-5555-4555-8555-555555555555",
        sequence_no: 3,
        event_type: "evidence_conflict",
        source_type: "database_verification",
        evidence_code: "NO_EFFECT_CONFLICTS_WITH_SUCCESS",
        evidence_fingerprint: secondFingerprint,
        related_evidence_fingerprint: firstFingerprint,
        superseded_status: "succeeded",
        observed_at: "2026-08-24T08:02:00.000Z",
      },
    );
    installApi([run(runAId, "cancellation_uncertain")], {
      cancellationByRun: { [runAId]: conflict },
    });
    render(<Dashboard />);

    expect(
      await screen.findByRole("heading", { name: "تعارض دليل النتيجة؛ المصالحة مطلوبة" }),
    ).toBeTruthy();
    expect(screen.getByText(firstFingerprint)).toBeTruthy();
    expect(screen.getByText(secondFingerprint)).toBeTruthy();
    expect(screen.getByText("succeeded")).toBeTruthy();
  });

  it("renders explicit, distinct visual tones at a narrow viewport", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 375 });
    installApi([
      run(runAId, "cancelled"),
      run(runBId, "partially_succeeded"),
      run("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "failed"),
      run("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "cancellation_uncertain"),
    ], {
      cancellationByRun: { [runAId]: cancellation(runAId, "cancelled") },
    });
    render(<Dashboard />);

    await screen.findByRole("heading", { name: "alpha.create" });
    const cancelledBadge = document.querySelector(".run-badge.status-cancelled")!;
    const partialBadge = document.querySelector(".run-badge.status-partially_succeeded")!;
    const failedBadge = document.querySelector(".run-badge.status-failed")!;
    const uncertainBadge = document.querySelector(".run-badge.status-cancellation_uncertain")!;
    expect(cancelledBadge.classList.contains("status-cancelled")).toBe(true);
    expect(partialBadge.classList.contains("status-partially_succeeded")).toBe(true);
    expect(failedBadge.classList.contains("status-failed")).toBe(true);
    expect(uncertainBadge.classList.contains("status-cancellation_uncertain")).toBe(true);
    expect(dashboardStyles).toContain('--font-ui: Calibri, "Segoe UI", Arial, sans-serif');
    expect(dashboardStyles).toContain(".status-partially_succeeded");
    expect(dashboardStyles).toContain(".status-cancellation_uncertain");
    expect(dashboardStyles).toContain(".tool-outcome_unknown");
    expect(dashboardStyles).toMatch(
      /\.conversation-list\s*\{[\s\S]*?overflow-x:\s*hidden/,
    );
    expect(screen.getByRole("heading", { name: "أُلغي قبل الأثر" })).toBeTruthy();
  });
});
