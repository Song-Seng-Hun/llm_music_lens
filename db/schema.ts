import { sqliteTable, text, integer, index, primaryKey } from "drizzle-orm/sqlite-core";

export const reports = sqliteTable("reports", {
  id: text("id").primaryKey(),
  ownerId: text("owner_id").notNull(),
  title: text("title").notNull(),
  sourceType: text("source_type").notNull(),
  createdAt: text("created_at").notNull(),
  body: text("body").notNull(),
}, (t) => [index("reports_owner_created").on(t.ownerId, t.createdAt)]);

export const analysisRequests = sqliteTable("analysis_requests", {
  ownerId: text("owner_id").notNull(),
  requestId: text("request_id").notNull(),
  digest: text("digest").notNull(),
  state: text("state").notNull(),
  reportId: text("report_id"),
  startedAt: integer("started_at").notNull(),
}, (t) => [primaryKey({ columns: [t.ownerId, t.requestId] })]);
