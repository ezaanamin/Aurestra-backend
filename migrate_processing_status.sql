-- Migration: Add processing status fields to statement_analysis table

-- Add processing_status column
ALTER TABLE statement_analysis ADD COLUMN processing_status VARCHAR(20) DEFAULT 'success';

-- Add processing_notes column  
ALTER TABLE statement_analysis ADD COLUMN processing_notes TEXT;

-- Update existing records to have default status
UPDATE statement_analysis SET processing_status = 'success' WHERE processing_status IS NULL;
