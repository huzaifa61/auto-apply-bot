/**
 * REPLACES your existing Apps Script code (Extensions > Apps Script in the
 * Sheet). Redeploy as a NEW deployment version after pasting this in, and
 * make sure the resulting /exec URL matches what's in both:
 *   - your Apify actor's `googleAppsScriptUrl` input
 *   - this project's .env `APPS_SCRIPT_URL`
 * (Redeploying with "Manage deployments" > edit > new version keeps the
 * same URL; creating a brand new deployment gives you a new URL you'd need
 * to update in both places.)
 *
 * What's new vs. the original:
 *   - Two new columns: "Application Status" and "Flag Reason".
 *   - GET ?action=queue        -> returns unprocessed jobs as JSON, for the
 *                                 apply_bot.py script to pull from.
 *   - POST {action:"updateStatus", url, status, reason, appliedAt}
 *                              -> marks a row Applied / Flagged / Failed and
 *                                 ticks the "Applied?" checkbox when status
 *                                 is "Applied".
 *   - POST with a `jobs` array and no `action` still works exactly as
 *     before (that's what the Apify actor sends) — fully backward
 *     compatible, nothing to change on the actor side.
 */

var SHEET_NAME = 'Jobs';
var HEADERS = [
  'Scraped At',
  'Source',
  'Company',
  'Job Title',
  'Location',
  'Posted Date',
  'URL',
  'Matched Batch Terms',
  'Applied?',
  'Application Status',
  'Flag Reason',
];
var CHECKBOX_ROWS_TO_PROVISION = 5000;
var URL_COL = HEADERS.indexOf('URL') + 1;
var APPLIED_COL = HEADERS.indexOf('Applied?') + 1;
var STATUS_COL = HEADERS.indexOf('Application Status') + 1;
var REASON_COL = HEADERS.indexOf('Flag Reason') + 1;

function getOrCreateSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight('bold');
    sheet.setFrozenRows(1);
  } else {
    // Migrate older sheets that don't yet have the two new columns.
    var existingHeaders = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    if (existingHeaders.indexOf('Application Status') === -1) {
      sheet.getRange(1, existingHeaders.length + 1, 1, 2).setValues([['Application Status', 'Flag Reason']]);
    }
  }
  return sheet;
}

function applyCheckboxValidation_(sheet) {
  var rule = SpreadsheetApp.newDataValidation().requireCheckbox().build();
  sheet.getRange(2, APPLIED_COL, CHECKBOX_ROWS_TO_PROVISION, 1).setDataValidation(rule);
}

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);

    if (body.action === 'updateStatus') {
      return handleUpdateStatus_(body);
    }

    // --- original behavior: append newly-scraped jobs from the Apify actor ---
    var jobs = body.jobs || [];
    var sheet = getOrCreateSheet_();

    var rows = jobs.map(function (j) {
      return [
        j.scrapedAt || '',
        j.source || '',
        j.company || '',
        j.jobTitle || '',
        j.location || '',
        j.postedDate || '',
        j.url || '',
        (j.matchedBatchTerms || []).join(', '),
        false, // Applied? — starts unticked
        '',    // Application Status
        '',    // Flag Reason
      ];
    });

    if (rows.length > 0) {
      sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, HEADERS.length).setValues(rows);
    }

    applyCheckboxValidation_(sheet);

    return ContentService.createTextOutput(
      JSON.stringify({ ok: true, added: rows.length }),
    ).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(
      JSON.stringify({ ok: false, error: String(err) }),
    ).setMimeType(ContentService.MimeType.JSON);
  }
}

function handleUpdateStatus_(body) {
  var sheet = getOrCreateSheet_();
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: 'Sheet is empty' }))
      .setMimeType(ContentService.MimeType.JSON);
  }
  var urls = sheet.getRange(2, URL_COL, lastRow - 1, 1).getValues();
  for (var i = 0; i < urls.length; i++) {
    if (urls[i][0] === body.url) {
      var row = i + 2;
      sheet.getRange(row, STATUS_COL).setValue(body.status || '');
      sheet.getRange(row, REASON_COL).setValue(body.reason || '');
      if (body.status === 'Applied') {
        sheet.getRange(row, APPLIED_COL).setValue(true);
      }
      return ContentService.createTextOutput(JSON.stringify({ ok: true, updatedRow: row }))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }
  return ContentService.createTextOutput(JSON.stringify({ ok: false, error: 'URL not found in sheet' }))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  var action = e && e.parameter && e.parameter.action;

  if (action === 'queue') {
    var sheet = getOrCreateSheet_();
    var lastRow = sheet.getLastRow();
    if (lastRow < 2) {
      return ContentService.createTextOutput(JSON.stringify({ ok: true, jobs: [] }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    var data = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
    var jobs = [];
    for (var i = 0; i < data.length; i++) {
      var row = data[i];
      var applied = row[APPLIED_COL - 1];
      var status = row[STATUS_COL - 1];
      // Queue = not yet applied, and not already Flagged (flagged jobs wait
      // for you to update the answer bank, then clear the Status cell
      // yourself to re-queue them).
      if (!applied && status !== 'Flagged') {
        jobs.push({
          scrapedAt: row[0],
          source: row[1],
          company: row[2],
          jobTitle: row[3],
          location: row[4],
          postedDate: row[5],
          url: row[6],
          matchedBatchTerms: row[7],
        });
      }
    }
    return ContentService.createTextOutput(JSON.stringify({ ok: true, jobs: jobs }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  return ContentService.createTextOutput('SWE Internship Scraper webhook is live.');
}
