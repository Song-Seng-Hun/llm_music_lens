CREATE TABLE `analysis_requests` (
	`owner_id` text NOT NULL,
	`request_id` text NOT NULL,
	`digest` text NOT NULL,
	`state` text NOT NULL,
	`report_id` text,
	`started_at` integer NOT NULL,
	PRIMARY KEY(`owner_id`, `request_id`)
);
--> statement-breakpoint
CREATE TABLE `reports` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_id` text NOT NULL,
	`title` text NOT NULL,
	`source_type` text NOT NULL,
	`created_at` text NOT NULL,
	`body` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `reports_owner_created` ON `reports` (`owner_id`,`created_at`);