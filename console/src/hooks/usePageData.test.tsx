import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";
import { usePageData } from "./usePageData";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("usePageData request identity", () => {
  it("clears stale route data and ignores a late response from the prior key", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const fetcher = vi.fn((id: string, _signal: AbortSignal) => (
      id === "case-a" ? first.promise : second.promise
    ));

    function Probe({ id }: { id: string }) {
      const view = usePageData((signal) => fetcher(id, signal), `case:${id}`, 60_000);
      return <span>{view.loading ? "loading" : view.data ?? view.error ?? "empty"}</span>;
    }

    let renderer: ReactTestRenderer | null = null;
    await act(async () => {
      renderer = create(<Probe id="case-a" />);
    });
    expect(renderer!.root.findByType("span").children).toEqual(["loading"]);

    await act(async () => {
      renderer!.update(<Probe id="case-b" />);
    });
    expect(renderer!.root.findByType("span").children).toEqual(["loading"]);

    await act(async () => {
      second.resolve("case-b-data");
      await second.promise;
    });
    expect(renderer!.root.findByType("span").children).toEqual(["case-b-data"]);

    await act(async () => {
      first.resolve("stale-case-a-data");
      await first.promise;
    });
    expect(renderer!.root.findByType("span").children).toEqual(["case-b-data"]);

    renderer!.unmount();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
