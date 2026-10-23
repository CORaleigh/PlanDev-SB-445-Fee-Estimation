// Labels only; stored choices and fee rules are unchanged.
// "external" uses public names; "internal" uses the retained source names.
export const OPTION_LABEL_MODE = "external";

// The preview banner does not change calculations or restrict access.
export const STAKEHOLDER_PREVIEW = {
  enabled: true,
  heading: "Preview: For review only",
  showSourceDates: false, // Enable only for internal review.
  message:
    "This tool provides a preliminary fee estimate based on the information "
    + "entered. It does not save estimates, track legal deadlines, or issue "
    + "invoices or final fee statements.",
};
