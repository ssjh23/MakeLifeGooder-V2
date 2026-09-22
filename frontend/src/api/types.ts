/**
 * Flat aliases for the generated schema.
 *
 * `openapi-typescript` nests every response and request body under
 * `components["schemas"]`, which is precise but unreadable in feature code.
 * This is the one file that knows the nested path; everything else imports a
 * name that reads the same as the Pydantic class it mirrors.
 */

import type { components } from "./generated/schema";

type Schemas = components["schemas"];

export type AccountInventory = Schemas["AccountInventory"];
export type ArchivePreview = Schemas["ArchivePreview"];
export type ArchiveRequest = Schemas["ArchiveRequest"];
export type CardBand = Schemas["CardBand"];
export type CardCreate = Schemas["CardCreate"];
export type CardResponse = Schemas["CardResponse"];
export type CardUpdate = Schemas["CardUpdate"];
export type CategoryBand = Schemas["CategoryBand"];
export type CategoryCreate = Schemas["CategoryCreate"];
export type CategoryDetail = Schemas["CategoryDetail"];
export type CategoryMergeRequest = Schemas["CategoryMergeRequest"];
export type CategoryResponse = Schemas["CategoryResponse"];
export type CategoryUpdate = Schemas["CategoryUpdate"];
export type ClassifyRequest = Schemas["ClassifyRequest"];
export type CommitRequest = Schemas["CommitRequest"];
export type CompanyRow = Schemas["CompanyRow"];
export type ConfirmAllRequest = Schemas["ConfirmAllRequest"];
export type DashboardResponse = Schemas["DashboardResponse"];
export type DeleteAccountRequest = Schemas["DeleteAccountRequest"];
export type DeleteStatementRequest = Schemas["DeleteStatementRequest"];
export type DeleteStatementsRequest = Schemas["DeleteStatementsRequest"];
export type DuplicatePair = Schemas["DuplicatePair"];
export type ExportRequest = Schemas["ExportRequest"];
export type ExportResponse = Schemas["ExportResponse"];
export type ExtractionReport = Schemas["ExtractionReport"];
export type ImportSummary = Schemas["ImportSummary"];
export type LoginRequest = Schemas["LoginRequest"];
export type ManualEntryRequest = Schemas["ManualEntryRequest"];
export type MeResponse = Schemas["MeResponse"];
export type MerchantGroup = Schemas["MerchantGroup"];
export type MonthBand = Schemas["MonthBand"];
export type MonthReapplyPreview = Schemas["MonthReapplyPreview"];
export type OverrideRequest = Schemas["OverrideRequest"];
export type OverrideResponse = Schemas["OverrideResponse"];
export type PasswordResetConfirm = Schemas["PasswordResetConfirm"];
export type PasswordResetRequest = Schemas["PasswordResetRequest"];
export type Period = Schemas["Period"];
export type Provenance = Schemas["Provenance"];
export type ReapplyPreview = Schemas["ReapplyPreview"];
export type ReapplyPreviewRequest = Schemas["ReapplyPreviewRequest"];
export type ReapplyRequest = Schemas["ReapplyRequest"];
export type Reconciliation = Schemas["Reconciliation"];
export type RegisterRequest = Schemas["RegisterRequest"];
export type ResolveConflictRequest = Schemas["ResolveConflictRequest"];
export type ReviewBoard = Schemas["ReviewBoard"];
export type ReviewFilters = Schemas["ReviewFilters"];
export type ReviewFooter = Schemas["ReviewFooter"];
export type ReviewResolveDuplicateRequest =
  Schemas["app__schemas__review__ResolveDuplicateRequest"];
export type RowCreate = Schemas["RowCreate"];
export type RowMutationResponse = Schemas["RowMutationResponse"];
export type RowResponse = Schemas["RowResponse"];
export type RowUpdate = Schemas["RowUpdate"];
export type RowsResponse = Schemas["RowsResponse"];
export type RuleConflictSummary = Schemas["RuleConflictSummary"];
export type RuleCreate = Schemas["RuleCreate"];
export type MatchTypeLiteral = RuleCreate["match_type"];
export type RuleOptions = Schemas["RuleOptions"];
export type RulePreview = Schemas["RulePreview"];
export type RulePreviewRequest = Schemas["RulePreviewRequest"];
export type RuleResponse = Schemas["RuleResponse"];
export type RuleUpdate = Schemas["RuleUpdate"];
export type StatementDisposition = Schemas["StatementDisposition"];
export type StatementFailure = Schemas["StatementFailure"];
export type StatementRegister = Schemas["StatementRegister"];
export type StatementResolveDuplicateRequest =
  Schemas["app__schemas__statements__ResolveDuplicateRequest"];
export type StatementResponse = Schemas["StatementResponse"];
export type StatementTag = Schemas["StatementTag"];
export type SuggestedCategory = Schemas["SuggestedCategory"];
export type TransactionResponse = Schemas["TransactionResponse"];
export type UnclassifiedBanner = Schemas["UnclassifiedBanner"];
export type UnlockRequest = Schemas["UnlockRequest"];
export type UploadUrlRequest = Schemas["UploadUrlRequest"];
export type UploadUrlResponse = Schemas["UploadUrlResponse"];
