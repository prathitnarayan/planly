// Mirrors the FastAPI response models (backend/app/...). Dates are ISO strings.

export type GoalSummary = {
  id: string;
  title: string;
  interview_done: boolean;
  has_blueprint: boolean;
  has_capacity: boolean;
  checkins: number;
  updated_at: string;
};

export type Benchmark = { key: string; what: string; hours: number; parts: Record<string, number> };
export type KeyDate = { key: string; label: string; date: string; hard: boolean };

export type GoalProfile = {
  current_level: string | null;
  target_outcome: string | null;
  deadline: string | null;
  key_dates: KeyDate[];
  benchmarks: Benchmark[];
  resources: string[];
  constraints: string[];
  notes: string | null;
};

export type GoalView = {
  id: string;
  goal: string;
  interview_done: boolean;
  question: string | null;
  questions_asked: number;
  profile: GoalProfile;
  summary: string[];
  has_blueprint: boolean;
  has_capacity: boolean;
  plan_start: string | null;
  checked_through: string | null;
};

export type GoalCheck = { is_goal: boolean; suggested_goal: string | null; background: string | null };

export type TaskKind = "learn" | "practice" | "project" | "revise" | "assess" | "buffer";

export type Milestone = {
  key: string;
  name: string;
  description: string;
  hours_by_kind: Partial<Record<TaskKind, number>>;
  confidence: number;
  depends_on: string[];
  done_criteria: string[];
  deliverable: string | null;
  required: boolean;
  start_after: string | null;
  due_by: string | null;
  due_offset_days: number;
};

export type Blueprint = {
  goal: string;
  summary: string;
  milestones: Milestone[];
  assumptions: string[];
  open_questions: string[];
  anchors: Record<string, string>;
};

export type EstimateWarning = {
  deliverable: string;
  ai_hours: number;
  benchmark_what: string;
  benchmark_hours: number;
  benchmark_parts: Record<string, number>;
  ratio: number;
  direction: "high" | "low";
  suggested_hours: number;
};
export type EstimateCheck = { warnings: EstimateWarning[]; messages: string[] };

export type Slot = { weekday: number; start: string; end: string };
export type Capacity = { slots: Slot[]; sustainable_ratio?: number };

export type MilestoneForecast = {
  key: string;
  name: string;
  hours: number;
  required: boolean;
  earliest_start: string | null;
  due: string | null;
  projected_finish: string | null;
  late: boolean;
  days_late: number | null;
  hard: boolean;
};

export type Feasibility = {
  feasible: boolean;
  required_hours: number;
  available_hours: number;
  last_due: string;
  projected_finish: string | null;
  milestones: MilestoneForecast[];
  late: string[];
  options: null | {
    extra_hours_per_week: number | null;
    extra_hours_realistic: boolean;
    earliest_finish: string | null;
    later_finish_ok: boolean;
    hard_late: string[];
    optional_cuts: string[];
    feasible_after_cuts: boolean;
  };
};

export type Session = {
  day: string;
  start: string | null;
  end: string | null;
  minutes: number;
  milestone_key: string;
  milestone_name: string;
  deliverable: string | null;
  kind: TaskKind;
};
export type DayPlan = { day: string; capacity_minutes: number; sessions: Session[] };
export type Sprint = {
  number: number;
  start: string;
  end: string;
  capacity_minutes: number;
  planned_minutes: number;
  working_on: string[];
  finishing: string[];
  definition_of_done: string[];
  days: DayPlan[];
};

export type MilestoneProgress = {
  key: string;
  name: string;
  estimate_minutes: number;
  spent_minutes: number;
  remaining_minutes: number;
  complete: boolean;
  overrun: boolean;
  sessions_missed: number;
};

export type Replan = {
  today: string;
  progress: MilestoneProgress[];
  multiplier: number | null;
  feasibility: Feasibility;
  schedule: { sprints: Sprint[]; finish: string | null; unscheduled_minutes: number };
  alerts: string[];
};

export type DueSession = { day: string; milestone_key: string; milestone_name: string; planned_minutes: number };
export type Due = { since: string; until: string; sessions: DueSession[]; message: string | null };

export type CheckIn = {
  day: string;
  milestone_key: string;
  outcome: "done" | "partial" | "missed";
  planned_minutes: number;
  actual_minutes: number;
  milestone_complete?: boolean;
};

export type SourceItem = {
  title: string; section: string | null; kind: string; minutes: number | null;
  duration_text: string | null; due: string | null; difficulty: string | null; done: boolean; url: string | null;
};
export type KindLoad = { kind: string; items: number; remaining: number; shown_minutes: number; study_minutes: number };
export type SourceLoad = {
  url: string; title: string; platform: string; items: number; done: number; study_minutes: number;
  by_kind: KindLoad[]; unsized: string[]; due: { title: string; kind: string; due: string }[];
};
export type SourceView = { load: SourceLoad; items: SourceItem[]; synced_at: string };
export type SyncResult = {
  load: SourceLoad; items: SourceItem[]; key_dates_added: string[]; key_dates_moved: string[]; messages: string[];
};

export type TodaySession = {
  id: string; day: string; start: string | null; end: string | null; minutes: number;
  milestone_key: string; milestone_name: string; deliverable: string | null; kind: string; done: boolean;
};
export type TodayView = {
  day: string; sessions: TodaySession[]; planned_minutes: number; done_minutes: number;
  moved: string[]; closed: boolean; message: string | null;
};
