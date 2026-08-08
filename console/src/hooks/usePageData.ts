import { useCallback, useEffect, useRef, useState } from "react";
import { subscribeRefresh } from "../lib/refreshBus";

export interface PageData<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** 手动刷新 */
  reload: () => void;
  /** 是否正在后台刷新（供顶栏刷新按钮转圈/去抖） */
  refreshing: boolean;
  /** 后台刷新失败时保留上次成功数据，并显式标记 stale。 */
  refreshError: string | null;
  lastUpdatedAt: string | null;
}

/**
 * 页面级数据钩子：首次加载 + 轮询（默认 10s）+ 手动刷新。
 * fetcher 由页面组合多个端点，保持单一 loading/error/reload 周期。
 * requestKey 必须包含 route/filter 参数；变化时旧请求会被中止且旧数据不会残留。
 */
export function usePageData<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  requestKey = "default",
  intervalMs = 10_000,
): PageData<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  const controllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const dataRef = useRef<T | null>(null);
  fetcherRef.current = fetcher;
  dataRef.current = data;

  const load = useCallback(async (isBackground: boolean) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const requestId = ++requestIdRef.current;
    if (isBackground) setRefreshing(true);
    try {
      const result = await fetcherRef.current(controller.signal);
      if (requestId !== requestIdRef.current) return;
      dataRef.current = result;
      setData(result);
      setError(null);
      setRefreshError(null);
      setLastUpdatedAt(new Date().toISOString());
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      if (requestId !== requestIdRef.current) return;
      const message = e instanceof Error ? e.message : String(e);
      if (dataRef.current === null) setError(message);
      else setRefreshError(message);
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    controllerRef.current?.abort();
    dataRef.current = null;
    setData(null);
    setLoading(true);
    setRefreshing(false);
    setError(null);
    setRefreshError(null);
    setLastUpdatedAt(null);
    void load(false);
    const timer = setInterval(() => void load(true), intervalMs);
    const unsub = subscribeRefresh(() => void load(true));
    return () => {
      controllerRef.current?.abort();
      clearInterval(timer);
      unsub();
    };
  }, [load, intervalMs, requestKey]);

  const reload = useCallback(() => void load(true), [load]);

  return { data, loading, error, reload, refreshing, refreshError, lastUpdatedAt };
}
