/**
 * Maps a backend review-status string to a localized (zh-CN) label.
 *
 * Shared between ExtractionPage and ExtractionRecordsPage so the badge text
 * stays consistent and does not drift between the two surfaces.
 */
export function reviewStatusLabel(status: string): string {
  switch (status) {
    case "corrected":
      return "已修正";
    case "approved":
      return "已通过";
    case "rejected":
      return "已驳回";
    case "reviewed":
      return "已复核";
    default:
      return "";
  }
}
