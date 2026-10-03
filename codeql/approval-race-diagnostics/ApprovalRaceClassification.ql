/**
 * Diagnose the official actions library's interpretation of the marimo checkout.
 * It is intentionally excluded from the baseline query suites.
 */
import actions
import codeql.actions.security.UntrustedCheckoutQuery
import codeql.actions.security.ControlChecks

from UsesStep checkout, Event event, string classification, string dateCheck
where
  checkout.getCallee() = "actions/checkout" and
  checkout.getEnclosingWorkflow().getName() = "marimo bot" and
  exists(checkout.getArgument("ref")) and
  event.getName() = "issue_comment" and
  checkout.getATriggerEvent() = event and
  (
    classification = "mutable-ref" and checkout instanceof MutableRefCheckoutStep
    or
    classification = "sha" and checkout instanceof SHACheckoutStep
    or
    classification = "unclassified" and
    not checkout instanceof MutableRefCheckoutStep and
    not checkout instanceof SHACheckoutStep
  ) and
  (
    dateCheck = "protects-toctou" and
    exists(CommentVsHeadDateCheck check |
      check.protects(checkout, event, "untrusted-checkout-toctou")
    )
    or
    dateCheck = "no-protecting-date-check" and
    not exists(CommentVsHeadDateCheck check |
      check.protects(checkout, event, "untrusted-checkout-toctou")
    )
  )
select checkout, checkout.getArgument("ref"), classification, dateCheck
