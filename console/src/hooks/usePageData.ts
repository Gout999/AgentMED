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
}

/**
 * 页面级数据钩子：首次加载 + 轮询（默认 10s）+ 手动刷新。
 * fetcher 由页面组合多个端点，保持单一 loading/error/reload 周期。
 */
export function usePageData<T>(fetcher: () => Promise<T>, intervalMs = 10_000): PageData<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async (isBackground: boolean) => {
    if (isBackground) setRefreshing(true);
    try {
      const result = await fetcherRef.current();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
    const timer = setInterval(() => void load(true), intervalMs);
    const unsub = subscribeRefresh(() => void load(true));
    return () => {
      clearInterval(timer);
      unsub();
    };
  }, [load, intervalMs]);

  const reload = useCallback(() => void load(true), [load]);

  return { data, loading, error, reload, refreshing };
}
