import { useEffect, useMemo, useState, type ComponentType } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock,
  Cpu,
  FileText,
  MessageSquare,
  PlayCircle,
  RefreshCw,
  Settings,
  Sparkles,
} from "lucide-react";

import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type { ModelInfoResponse, SessionInfo, StatusResponse } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

type LoadState = {
  error: string | null;
  loading: boolean;
  model: ModelInfoResponse | null;
  sessions: SessionInfo[];
  status: StatusResponse | null;
};

const INITIAL_STATE: LoadState = {
  error: null,
  loading: true,
  model: null,
  sessions: [],
  status: null,
};

function statusTone(status: StatusResponse | null): {
  label: string;
  tone: "good" | "muted" | "warning";
} {
  if (!status) return { label: "確認中", tone: "muted" };
  if (status.gateway_state === "startup_failed") return { label: "起動失敗", tone: "warning" };
  if (status.gateway_running) return { label: "実行中", tone: "good" };
  return { label: "停止中", tone: "muted" };
}

function toneClass(tone: "good" | "muted" | "warning") {
  if (tone === "good") return "border-success/30 bg-success/10 text-success";
  if (tone === "warning") return "border-warning/40 bg-warning/10 text-warning";
  return "border-current/15 bg-card/50 text-muted-foreground";
}

function StatCard({
  icon: Icon,
  label,
  value,
  detail,
  tone = "muted",
}: {
  detail: string;
  icon: ComponentType<{ className?: string }>;
  label: string;
  tone?: "good" | "muted" | "warning";
  value: string;
}) {
  return (
    <div className="rounded-xl border border-current/10 bg-card/60 p-4 shadow-sm backdrop-blur-sm">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
          {label}
        </p>
        <span className={cn("rounded-full border p-2", toneClass(tone))}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="mt-4 truncate text-2xl font-semibold tracking-tight text-foreground">
        {value}
      </p>
      <p className="mt-1 truncate text-sm text-muted-foreground">{detail}</p>
    </div>
  );
}

function QuickLink({
  description,
  icon: Icon,
  label,
  to,
}: {
  description: string;
  icon: ComponentType<{ className?: string }>;
  label: string;
  to: string;
}) {
  return (
    <Link
      to={to}
      className="group rounded-xl border border-current/10 bg-card/50 p-4 transition hover:border-primary/35 hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
    >
      <div className="flex items-start gap-3">
        <span className="rounded-lg border border-primary/20 bg-primary/10 p-2 text-primary transition group-hover:bg-primary/15">
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">{label}</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        </div>
      </div>
    </Link>
  );
}

function EmptyRecentSessions() {
  return (
    <div className="rounded-xl border border-dashed border-current/15 bg-card/30 p-6 text-center">
      <Sparkles className="mx-auto h-6 w-6 text-muted-foreground" />
      <p className="mt-3 text-sm font-medium text-foreground">まだセッションがありません</p>
      <p className="mt-1 text-xs text-muted-foreground">
        CLI・Discord・Dashboard Chat で会話するとここに表示されます。
      </p>
    </div>
  );
}

export default function DashboardPage() {
  const [state, setState] = useState<LoadState>(INITIAL_STATE);
  const { setEnd, setTitle } = usePageHeader();

  const load = async () => {
    setState((current) => ({ ...current, error: null, loading: true }));
    try {
      const [status, sessionsPage, model] = await Promise.all([
        api.getStatus(),
        api.getSessions(5, 0),
        api.getModelInfo(),
      ]);
      setState({
        error: null,
        loading: false,
        model,
        sessions: sessionsPage.sessions,
        status,
      });
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
        loading: false,
      }));
    }
  };

  useEffect(() => {
    setTitle("Dashboard");
    return () => {
      setTitle(null);
    };
  }, [setTitle]);

  useEffect(() => {
    setEnd(
      <button
        type="button"
        onClick={() => void load()}
        className="inline-flex items-center gap-2 rounded-lg border border-current/15 bg-card/60 px-3 py-2 text-xs font-medium text-foreground transition hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
      >
        <RefreshCw className={cn("h-3.5 w-3.5", state.loading && "animate-spin")} />
        更新
      </button>,
    );
    return () => setEnd(null);
  }, [setEnd, state.loading]);

  useEffect(() => {
    void load();
  }, []);

  const gateway = useMemo(() => statusTone(state.status), [state.status]);
  const connectedPlatforms = state.status
    ? Object.values(state.status.gateway_platforms ?? {}).filter(
        (platform) => platform.state === "connected" || platform.state === "running",
      ).length
    : 0;

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-1 py-2 sm:px-0 sm:py-4">
      <section className="rounded-2xl border border-current/10 bg-card/50 p-5 backdrop-blur-sm sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
              <Bot className="h-3.5 w-3.5" />
              Sinria Control Center
            </div>
            <h2 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
              必要な状態だけ、すぐ分かる。
            </h2>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-muted-foreground sm:text-base">
              CLIを開かなくても、セッション・モデル・Gateway・ログへ最短で移動できるシンプルなダッシュボードです。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              to="/chat"
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90"
            >
              <PlayCircle className="h-4 w-4" />
              Chatを開く
            </Link>
            <Link
              to="/sessions"
              className="inline-flex items-center gap-2 rounded-lg border border-current/15 bg-card/70 px-4 py-2 text-sm font-semibold text-foreground transition hover:bg-accent"
            >
              <MessageSquare className="h-4 w-4" />
              履歴を見る
            </Link>
          </div>
        </div>
      </section>

      {state.error ? (
        <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 text-warning">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
            <div>
              <p className="font-medium">Dashboard データの取得に失敗しました</p>
              <p className="mt-1 text-sm text-warning/80">{state.error}</p>
            </div>
          </div>
        </div>
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          icon={gateway.tone === "warning" ? AlertTriangle : CheckCircle2}
          label="Gateway"
          value={gateway.label}
          detail={
            state.status?.gateway_pid
              ? `PID ${state.status.gateway_pid}`
              : connectedPlatforms > 0
                ? `${connectedPlatforms} platforms`
                : "local status"
          }
          tone={gateway.tone}
        />
        <StatCard
          icon={MessageSquare}
          label="Active sessions"
          value={String(state.status?.active_sessions ?? "—")}
          detail="直近5分で動いている会話"
          tone={(state.status?.active_sessions ?? 0) > 0 ? "good" : "muted"}
        />
        <StatCard
          icon={Cpu}
          label="Model"
          value={state.model?.model || "—"}
          detail={state.model?.provider ? `provider: ${state.model.provider}` : "未取得"}
          tone="muted"
        />
        <StatCard
          icon={Activity}
          label="Version"
          value={state.status?.version || "—"}
          detail={state.status?.hermes_home || "Sinria home"}
          tone="muted"
        />
      </section>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="rounded-2xl border border-current/10 bg-card/50 p-5 backdrop-blur-sm">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-foreground">クイックアクセス</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                よく使う操作だけを大きく配置しています。
              </p>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <QuickLink
              to="/chat"
              icon={PlayCircle}
              label="Chat"
              description="ブラウザから Sinria を操作する"
            />
            <QuickLink
              to="/sessions"
              icon={MessageSquare}
              label="Sessions"
              description="過去の会話・実行履歴を確認する"
            />
            <QuickLink
              to="/models"
              icon={Cpu}
              label="Models"
              description="利用中モデルとプロバイダを確認する"
            />
            <QuickLink
              to="/logs"
              icon={FileText}
              label="Logs"
              description="Gatewayやエラーのログを見る"
            />
            <QuickLink
              to="/cron"
              icon={Clock}
              label="Cron"
              description="自律実行・定期実行ジョブを管理する"
            />
            <QuickLink
              to="/config"
              icon={Settings}
              label="Config"
              description="設定をGUIから調整する"
            />
          </div>
        </div>

        <div className="rounded-2xl border border-current/10 bg-card/50 p-5 backdrop-blur-sm">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-foreground">最近のセッション</h3>
              <p className="mt-1 text-sm text-muted-foreground">最新5件を表示</p>
            </div>
            <Link to="/sessions" className="text-xs font-medium text-primary hover:underline">
              すべて見る
            </Link>
          </div>

          {state.sessions.length === 0 ? (
            <EmptyRecentSessions />
          ) : (
            <div className="space-y-2">
              {state.sessions.map((session) => (
                <Link
                  key={session.id}
                  to="/sessions"
                  className="block rounded-xl border border-current/10 bg-background/30 p-3 transition hover:border-primary/25 hover:bg-accent/50"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">
                        {session.title || "無題のセッション"}
                      </p>
                      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                        {session.preview || `${session.message_count} messages / ${session.tool_call_count} tools`}
                      </p>
                    </div>
                    <span
                      className={cn(
                        "mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium",
                        session.is_active
                          ? "bg-success/10 text-success"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {session.is_active ? "active" : timeAgo(session.last_active)}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
