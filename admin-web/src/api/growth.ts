// growth API（WM7：成长档案/测验重置/积分调整/等级重算）
import { request } from "./client";

export interface GrowthSummary {
  child_id: number;
  child_name: string;
  words_total: number;
  books_total: number;
  points_total: number;
  words_remainder: number;
  level: string;
  level_books_threshold: number;
  progress_in_level: number;
  milestone_nodes: number[];
  milestones_awarded: number[];
  is_z_capped: boolean;
}

export interface WordsLedgerItem {
  id: number;
  book_id: number;
  title: string;
  word_count: number;
  created_at: string;
}

export interface PointLedgerItem {
  id: number;
  points: number;
  reason_type: string;
  detail: string;
  created_at: string;
}

export interface QuizOverviewItem {
  book_id: number;
  title: string;
  attempts_used: number;
  best_score: number;
  max_attempts: number;
  passed: boolean;
}

export interface ChildGrowth {
  summary: GrowthSummary;
  words_ledger: WordsLedgerItem[];
  points_ledger: PointLedgerItem[];
  quiz_overview: QuizOverviewItem[];
}

export function apiGetChildGrowth(childId: number): Promise<ChildGrowth> {
  return request(`/api/admin/children/${childId}/growth`);
}

export function apiResetQuizAttempts(body: {
  child_id: number; book_id: number; reason: string;
}): Promise<{ cleared: number; attempts_left: number }> {
  return request("/api/admin/quiz/attempts/reset", { method: "POST", body: JSON.stringify(body) });
}

export function apiAdjustPoints(childId: number, body: {
  points: number; reason: string;
}): Promise<{ points_total: number }> {
  return request(`/api/admin/children/${childId}/points/adjust`, {
    method: "POST", body: JSON.stringify(body),
  });
}

export function apiRecalcLevels(): Promise<{
  threshold: number; states: number; level_changed: number; milestone_new: number;
}> {
  return request("/api/admin/growth/levels/recalc", { method: "POST" });
}

export function apiCheckMilestones(childId: number): Promise<{ new_nodes: number[] }> {
  return request(`/api/admin/children/${childId}/milestones/check`, { method: "POST" });
}
