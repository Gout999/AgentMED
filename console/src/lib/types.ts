/** control-plane REST 响应类型（对齐 console/DATA-MAP.md，以实际运行数据为准）。 */

export interface ListResponse<T> {
  items: T[];
  next_cursor: number | null;
}

/** GET /v1/cases 列表项 */
export interface CaseSummary {
  case_id: string;
  state: string;
  revision: number;
  updated_at: string | null;
}

/** GET /v1/cases/{id} 详情 */
export interface CaseDetail {
  case_id: string;
  state: string;
  revision: number;
  payload: Record<string, unknown>;
  updated_at: string | null;
  event_count: number;
}

/** GET /v1/experiments 列表项 / 详情 */
export interface Experiment {
  experiment_id: string;
  state: string;
  revision: number;
  payload: Record<string, unknown>;
}

/** GET /v1/changesets 列表项（审批队列 / 历史审批记录） */
export interface ChangeSet {
  changeset_id: string;
  state: string;
  revision: number;
  payload: Record<string, unknown>;
}

/** GET /v1/releases 列表项 */
export interface Release {
  release_id: string;
  state: string;
  revision: number;
}

/** GET /healthz */
export interface Healthz {
  status: string;
  version: string;
}
