import { createSignal, onMount, onCleanup, Show, For, createEffect } from "solid-js";
import { reconcile } from "solid-js/store";
import {
  clearSession,
  createSubmission,
  fetchReturnHistory,
  fetchSubmission,
  fetchSubmissionHistory,
  fetchSubmissions,
  getUser,
  login,
  returnSubmission,
  setSession,
} from "./api";

const statusLabel = {
  pending: "待复核",
  processing: "复核中",
  done: "已结清",
};

const roleLabel = {
  machinist: "操作员",
  auditor: "复核员",
};

const POLL_MS = 3000;

function readHash() {
  const raw = (location.hash || "#/").replace(/^#/, "") || "/";
  const m = raw.match(/^\/detail\/(\d+)/);
  if (m) return { name: "detail", id: Number(m[1]) };
  return { name: "home", id: null };
}

function App() {
  const [user, setUser] = createSignal(getUser());
  const [rows, setRows] = createSignal([]);
  const [detail, setDetail] = createSignal(null);
  const [route, setRoute] = createSignal(readHash());
  const [error, setError] = createSignal("");
  const [loading, setLoading] = createSignal(false);

  const [loginUser, setLoginUser] = createSignal("machinist");
  const [loginPass, setLoginPass] = createSignal("machine123456");

  const [toolCode, setToolCode] = createSignal("");
  const [offsetUm, setOffsetUm] = createSignal("");

  // 打回台状态
  const [returnHistory, setReturnHistory] = createSignal([]);
  const [reasonDrafts, setReasonDrafts] = createSignal({});
  const [returningId, setReturningId] = createSignal(null);
  const [sideError, setSideError] = createSignal("");
  const [detailHistory, setDetailHistory] = createSignal([]);

  const isAuditor = () => Boolean(user() && user().can_review);
  const returnable = () => rows().filter((r) => r.status === "done");

  function goHome() {
    location.hash = "#/";
  }

  function goDetail(id) {
    location.hash = `#/detail/${id}`;
  }

  function setDraft(id, value) {
    setReasonDrafts((d) => ({ ...d, [id]: value }));
  }

  async function loadRows() {
    setLoading(true);
    setError("");
    try {
      // reconcile 按 id 就地更新，避免轮询重建行 DOM 导致原因框失焦
      setRows(reconcile(await fetchSubmissions(), { key: "id" }));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadHistory() {
    try {
      setReturnHistory(reconcile(await fetchReturnHistory(), { key: "id" }));
    } catch (e) {
      // 轮询时履历拉取失败不打断主界面
      setSideError(e.message);
    }
  }

  async function loadDetail(id) {
    setLoading(true);
    setError("");
    try {
      setDetail(await fetchSubmission(id));
      setDetailHistory(await fetchSubmissionHistory(id));
    } catch (e) {
      setError(e.message);
      setDetail(null);
      setDetailHistory([]);
    } finally {
      setLoading(false);
    }
  }

  async function refreshDesk() {
    if (!isAuditor()) return;
    try {
      const [list, history] = await Promise.all([
        fetchSubmissions(),
        fetchReturnHistory(),
      ]);
      setRows(reconcile(list, { key: "id" }));
      setReturnHistory(reconcile(history, { key: "id" }));
    } catch (e) {
      setSideError(e.message);
    }
  }

  onMount(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    if (user()) {
      if (route().name === "detail") loadDetail(route().id);
      else loadRows();
      if (isAuditor()) loadHistory();
    }
    // 复核员侧栏：轮询看待复核单子被 worker 再次认领结清
    const timer = setInterval(() => {
      if (!isAuditor()) return;
      if (route().name === "home") refreshDesk();
      if (route().name === "detail" && route().id) loadDetail(route().id);
    }, POLL_MS);
    onCleanup(() => {
      window.removeEventListener("hashchange", onHash);
      clearInterval(timer);
    });
  });

  createEffect(() => {
    const r = route();
    if (!user()) return;
    if (r.name === "detail" && r.id) loadDetail(r.id);
    if (r.name === "home") loadRows();
  });

  async function handleLogin(e) {
    e.preventDefault();
    setError("");
    try {
      const data = await login(loginUser(), loginPass());
      setSession(data.token, {
        username: data.username,
        role: data.role,
        can_write: data.can_write,
        can_review: data.can_review,
      });
      setUser(getUser());
      goHome();
      await loadRows();
      if (data.can_review) await loadHistory();
    } catch (err) {
      setError(err.message);
    }
  }

  function handleLogout() {
    clearSession();
    setUser(null);
    setRows([]);
    setDetail(null);
    setReturnHistory([]);
    setDetailHistory([]);
    setReasonDrafts({});
    goHome();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    try {
      await createSubmission(toolCode(), offsetUm());
      setToolCode("");
      setOffsetUm("");
      await loadRows();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleReturn(id) {
    const reason = (reasonDrafts()[id] || "").trim();
    setSideError("");
    if (!reason) {
      setSideError(`请先填写 #${id} 的打回原因`);
      return;
    }
    setReturningId(id);
    try {
      // 后端在同一事务内清结论、回待复核、计数加一、写履历
      await returnSubmission(id, reason);
      setDraft(id, "");
      await refreshDesk();
    } catch (err) {
      setSideError(err.message);
      await refreshDesk();
    } finally {
      setReturningId(null);
    }
  }

  return (
    <div class="page">
      <header class="topbar">
        <div class="brand">
          <h1>数控刀补复核台</h1>
          <p class="hint">刀补绝对值不超过十二微米判合格，否则超差。后台认领进程用行锁跳过已占行领取待复核。</p>
        </div>
        <Show when={user()}>
          <nav class="topnav">
            <a
              href="#/"
              class={route().name === "home" ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                goHome();
              }}
            >
              复核总览
            </a>
          </nav>
        </Show>
      </header>

      <Show when={error()}>
        <div class="banner error">{error()}</div>
      </Show>

      <Show
        when={user()}
        fallback={
          <section class="card">
            <h2>登录</h2>
            <form onSubmit={handleLogin} class="form">
              <label>
                用户名
                <input
                  value={loginUser()}
                  onInput={(e) => setLoginUser(e.currentTarget.value)}
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginPass()}
                  onInput={(e) => setLoginPass(e.currentTarget.value)}
                />
              </label>
              <button type="submit">进入系统</button>
            </form>
            <p class="hint">操作员 machinist / machine123456；复核员 auditor / audit123456（可打回再审）</p>
          </section>
        }
      >
        <section class="card toolbar">
          <div>
            当前用户：<strong>{user().username}</strong>（{roleLabel[user().role] || user().role}）
          </div>
          <button type="button" class="ghost" onClick={handleLogout}>
            退出
          </button>
        </section>

        <div class={isAuditor() ? "layout with-sidebar" : "layout"}>
          <main class="main-col">
            <Show when={route().name === "home"}>
              <Show when={user().can_write}>
                <section class="card">
                  <h2>提交刀补</h2>
                  <form onSubmit={handleSubmit} class="form inline">
                    <label>
                      刀具编号
                      <input
                        placeholder="如 T01"
                        value={toolCode()}
                        onInput={(e) => setToolCode(e.currentTarget.value)}
                        required
                      />
                    </label>
                    <label>
                      刀补（微米）
                      <input
                        type="number"
                        value={offsetUm()}
                        onInput={(e) => setOffsetUm(e.currentTarget.value)}
                        required
                      />
                    </label>
                    <button type="submit">提交待复核</button>
                  </form>
                </section>
              </Show>

              <section class="card">
                <div class="toolbar">
                  <h2>复核列表</h2>
                  <button type="button" class="ghost" onClick={loadRows} disabled={loading()}>
                    {loading() ? "刷新中…" : "刷新"}
                  </button>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>刀具</th>
                      <th>刀补 µm</th>
                      <th>状态</th>
                      <th>结论</th>
                      <th>打回</th>
                      <th>提交时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <For each={rows()}>
                      {(row) => (
                        <tr>
                          <td>{row.tool_code}</td>
                          <td>{row.offset_um}</td>
                          <td>{statusLabel[row.status] || row.status}</td>
                          <td class={row.verdict === "合格" ? "pass" : row.verdict === "超差" ? "fail" : ""}>
                            {row.verdict || "—"}
                          </td>
                          <td>{row.return_count ? `${row.return_count} 次` : "—"}</td>
                          <td>{new Date(row.created_at).toLocaleString()}</td>
                          <td>
                            <button type="button" class="ghost" onClick={() => goDetail(row.id)}>
                              详情
                            </button>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
                <Show when={!rows().length && !loading()}>
                  <p class="hint">暂无记录</p>
                </Show>
              </section>
            </Show>

            <Show when={route().name === "detail"}>
              <section class="card">
                <div class="toolbar">
                  <h2>刀补详情</h2>
                  <button type="button" class="ghost" onClick={goHome}>
                    返回总览
                  </button>
                </div>
                <Show when={detail()} fallback={<p class="hint">{loading() ? "加载中…" : "未找到记录"}</p>}>
                  {(d) => (
                    <>
                      <div class="detail-grid">
                        <p>编号：{d().id}</p>
                        <p>刀具：{d().tool_code}</p>
                        <p>刀补 µm：{d().offset_um}</p>
                        <p>状态：{statusLabel[d().status] || d().status}</p>
                        <p class={d().verdict === "合格" ? "pass" : d().verdict === "超差" ? "fail" : ""}>
                          结论：{d().verdict || "—"}
                        </p>
                        <p>打回次数：{d().return_count}</p>
                        <p>提交时间：{new Date(d().created_at).toLocaleString()}</p>
                        <p>
                          复核时间：
                          {d().reviewed_at ? new Date(d().reviewed_at).toLocaleString() : "—"}
                        </p>
                      </div>

                      <h3 class="history-title">打回履历</h3>
                      <Show when={detailHistory().length} fallback={<p class="hint">暂无打回记录</p>}>
                        <ul class="history-list">
                          <For each={detailHistory()}>
                            {(h) => (
                              <li class="history-item">
                                <div class="history-head">
                                  <span class="badge">第 {h.return_count} 次打回</span>
                                  <span class="hint">{new Date(h.created_at).toLocaleString()}</span>
                                </div>
                                <p class="history-reason">{h.reason}</p>
                                <p class="hint">
                                  打回人：{h.returned_by || "—"} · 当前状态：
                                  {statusLabel[h.current_status] || h.current_status}
                                </p>
                              </li>
                            )}
                          </For>
                        </ul>
                      </Show>
                    </>
                  )}
                </Show>
              </section>
            </Show>
          </main>

          <Show when={isAuditor()}>
            <aside class="sidebar">
              <section class="card return-desk">
                <h2>打回台</h2>
                <p class="hint">仅复核员可对「已结清」记录发起打回，须写明原因。</p>
                <Show when={sideError()}>
                  <div class="banner error">{sideError()}</div>
                </Show>

                <h3>可打回清单</h3>
                <Show when={returnable().length} fallback={<p class="hint">暂无可打回记录</p>}>
                  <ul class="return-list">
                    <For each={returnable()}>
                      {(row) => (
                        <li class="return-item">
                          <div class="return-meta">
                            <strong>{row.tool_code}</strong>
                            <span>{row.offset_um} µm</span>
                            <span class={row.verdict === "合格" ? "pass" : "fail"}>
                              {row.verdict}
                            </span>
                            <Show when={row.return_count}>
                              <span class="hint">（已打回 {row.return_count} 次）</span>
                            </Show>
                          </div>
                          <textarea
                            class="reason-box"
                            rows="2"
                            placeholder="打回原因（必填）"
                            value={reasonDrafts()[row.id] || ""}
                            onInput={(e) => setDraft(row.id, e.currentTarget.value)}
                          />
                          <button
                            type="button"
                            class="danger"
                            disabled={returningId() === row.id}
                            onClick={() => handleReturn(row.id)}
                          >
                            {returningId() === row.id ? "打回中…" : "打回再审"}
                          </button>
                        </li>
                      )}
                    </For>
                  </ul>
                </Show>

                <h3>打回记录</h3>
                <Show when={returnHistory().length} fallback={<p class="hint">暂无打回记录</p>}>
                  <ul class="history-list">
                    <For each={returnHistory()}>
                      {(h) => (
                        <li class="history-item">
                          <div class="history-head">
                            <span class="badge">第 {h.return_count} 次</span>
                            <span class="hint">{new Date(h.created_at).toLocaleString()}</span>
                          </div>
                          <p class="history-reason">{h.reason}</p>
                          <p class="hint">
                            {h.tool_code} · {h.returned_by || "—"} · 当前：
                            {statusLabel[h.current_status] || h.current_status}
                          </p>
                          <button type="button" class="ghost link" onClick={() => goDetail(h.submission_id)}>
                            查看详情
                          </button>
                        </li>
                      )}
                    </For>
                  </ul>
                </Show>
              </section>
            </aside>
          </Show>
        </div>
      </Show>
    </div>
  );
}

export default App;
