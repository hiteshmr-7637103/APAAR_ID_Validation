export type Role =
  "AICTE_ADMIN" | "AICTE_REVIEWER" | "INST_MAKER" | "INST_CHECKER" | "VERIFIER" | "STUDENT";

export type Area = "aicte" | "institution" | "verifier" | "student";

/** Which portal each role lands in after login. */
export const ROLE_AREA: Record<Role, Area> = {
  AICTE_ADMIN: "aicte",
  AICTE_REVIEWER: "aicte",
  INST_MAKER: "institution",
  INST_CHECKER: "institution",
  VERIFIER: "verifier",
  STUDENT: "student",
};

export const AREA_ROLES: Record<Area, Role[]> = {
  aicte: ["AICTE_ADMIN", "AICTE_REVIEWER"],
  institution: ["INST_MAKER", "INST_CHECKER"],
  verifier: ["VERIFIER"],
  student: ["STUDENT"],
};

export function homePath(role: Role): string {
  return `/${ROLE_AREA[role]}`;
}

export interface NavItem {
  label: string;
  phase: number; // build phase in which the screen arrives (spec section 15)
  roles?: Role[]; // restrict within an area (e.g. admin-only)
  path?: string; // set once the screen exists
}

/** Navigation per portal. Items without a `path` are screens of later phases. */
export const AREA_NAV: Record<Area, NavItem[]> = {
  aicte: [
    { label: "National dashboard", phase: 6, path: "/aicte/dashboard" },
    { label: "Review queue", phase: 5, path: "/aicte/review" },
    { label: "Reports", phase: 6, path: "/aicte/reports" },
    { label: "Audit log", phase: 4, path: "/aicte/audit" },
    { label: "Quota rules editor", phase: 8, roles: ["AICTE_ADMIN"] },
    { label: "Demo mode", phase: 8, roles: ["AICTE_ADMIN"] },
    { label: "Evaluation", phase: 8, roles: ["AICTE_ADMIN"] },
  ],
  institution: [
    { label: "Institution dashboard", phase: 4, path: "/institution/dashboard" },
    {
      label: "Student record form",
      phase: 3,
      roles: ["INST_MAKER"],
      path: "/institution/records/new",
    },
    { label: "Upload wizard", phase: 4, roles: ["INST_MAKER"], path: "/institution/uploads" },
    { label: "Approvals", phase: 4, roles: ["INST_CHECKER"], path: "/institution/approvals" },
    { label: "Cases", phase: 5, path: "/institution/cases" },
  ],
  verifier: [{ label: "Verification requests", phase: 7 }],
  student: [
    { label: "My enrolments", phase: 7 },
    { label: "Consent requests", phase: 7 },
    { label: "Access log", phase: 7 },
  ],
};

export const AREA_TITLE: Record<Area, string> = {
  aicte: "AICTE Portal",
  institution: "Institution Portal",
  verifier: "Verifier Portal",
  student: "Student Portal",
};
