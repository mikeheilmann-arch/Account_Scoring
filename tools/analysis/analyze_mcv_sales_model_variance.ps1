param(
    [Parameter(Mandatory = $true)]
    [string]$InputDirectory
)

$ErrorActionPreference = 'Stop'
$culture = [System.Globalization.CultureInfo]::InvariantCulture

function Get-Number {
    param([object]$Value)
    $parsed = 0.0
    if ($null -ne $Value -and [double]::TryParse([string]$Value, [System.Globalization.NumberStyles]::Any, $culture, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function Get-DateValue {
    param([object]$Value)
    if ([string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    return [datetimeoffset]::Parse([string]$Value, $culture)
}

function First-Positive {
    param([object[]]$Candidates)
    foreach ($candidate in $Candidates) {
        $number = Get-Number $candidate
        if ($null -ne $number -and $number -gt 0) { return $number }
    }
    return $null
}

$accounts = Import-Csv -LiteralPath (Join-Path $InputDirectory 'accounts.csv')
$opportunities = Import-Csv -LiteralPath (Join-Path $InputDirectory 'opportunities.csv') |
    Where-Object { $_.Type -eq 'New Business' }
$leads = Import-Csv -LiteralPath (Join-Path $InputDirectory 'converted_leads.csv')

$latestOpportunityByAccount = @{}
foreach ($group in ($opportunities | Group-Object AccountId)) {
    $latest = $group.Group |
        Where-Object { $null -ne (First-Positive @($_.Monthly_Closing_Volume__c, $_.Rep_Qualified_Monthly_Closings__c)) } |
        Sort-Object { Get-DateValue $_.CreatedDate } -Descending |
        Select-Object -First 1
    if ($null -ne $latest) { $latestOpportunityByAccount[$group.Name] = $latest }
}

$latestSalesLeadByAccount = @{}
foreach ($group in ($leads | Group-Object ConvertedAccountId)) {
    $latest = $group.Group |
        Where-Object {
            $rep = Get-Number $_.Rep_Qualified_Monthly_Closings__c
            $final = Get-Number $_.Final_Monthly_Closing_Volume__c
            ($null -ne $rep -and $rep -gt 0) -or
            (($null -ne $final -and $final -gt 0) -and $_.Monthly_Closing_Volume_Source__c -match 'Sales')
        } |
        Sort-Object { Get-DateValue $_.ConvertedDate } -Descending |
        Select-Object -First 1
    if ($null -ne $latest) { $latestSalesLeadByAccount[$group.Name] = $latest }
}

$now = [datetimeoffset]::UtcNow
$results = foreach ($account in $accounts) {
    $model = Get-Number $account.AI_Prospect_Value_MCV_Point__c
    if ($null -eq $model -or $model -le 0) { continue }

    $modelLow = Get-Number $account.AI_Prospect_Value_MCV_Low__c
    $modelHigh = Get-Number $account.AI_Prospect_Value_MCV_High__c
    $opportunity = $latestOpportunityByAccount[$account.Id]
    $lead = $latestSalesLeadByAccount[$account.Id]

    $opportunityMcv = if ($null -ne $opportunity) {
        First-Positive @($opportunity.Monthly_Closing_Volume__c, $opportunity.Rep_Qualified_Monthly_Closings__c)
    } else { $null }
    $accountLatestOppMcv = Get-Number $account.Latest_New_Business_Opp_MCV__c
    $accountRepMcv = Get-Number $account.Rep_Qualified_Monthly_Closings__c
    $leadMcv = if ($null -ne $lead) {
        First-Positive @($lead.Rep_Qualified_Monthly_Closings__c, $lead.Final_Monthly_Closing_Volume__c)
    } else { $null }
    $accountFinalSalesMcv = if ($account.Monthly_Closing_Volume_Source__c -match 'Sales') {
        Get-Number $account.Final_Monthly_Closing_Volume__c
    } else { $null }

    $salesMcv = $null
    $salesSource = $null
    $salesRecordId = $null
    $salesDate = $null
    $salesStage = $null
    if ($null -ne $opportunityMcv) {
        $salesMcv = $opportunityMcv
        $salesSource = 'Latest New Business Opportunity'
        $salesRecordId = $opportunity.Id
        $salesDate = Get-DateValue $opportunity.CreatedDate
        $salesStage = $opportunity.StageName
    } elseif ($null -ne $accountLatestOppMcv -and $accountLatestOppMcv -gt 0) {
        $salesMcv = $accountLatestOppMcv
        $salesSource = 'Account Latest New Business Opp MCV'
        $salesRecordId = $account.Latest_New_Biz_Opp_ID__c
        $salesDate = Get-DateValue $account.Latest_New_Business_Opp_Created_Date__c
        $salesStage = $account.Latest_New_Business_Opp_Stage__c
    } elseif ($null -ne $accountRepMcv -and $accountRepMcv -gt 0) {
        $salesMcv = $accountRepMcv
        $salesSource = 'Account Rep Qualified MCV'
    } elseif ($null -ne $leadMcv) {
        $salesMcv = $leadMcv
        $salesSource = 'Converted Lead Sales MCV'
        $salesRecordId = $lead.Id
        $salesDate = Get-DateValue $lead.ConvertedDate
    } elseif ($null -ne $accountFinalSalesMcv -and $accountFinalSalesMcv -gt 0) {
        $salesMcv = $accountFinalSalesMcv
        $salesSource = 'Account Final MCV - Sales Source'
    }

    if ($null -eq $salesMcv -or $salesMcv -le 0) { continue }

    $ratio = $model / $salesMcv
    $absoluteDelta = $model - $salesMcv
    $salesAgeMonths = if ($null -ne $salesDate) {
        [math]::Round(($now - $salesDate).TotalDays / 30.4375, 1)
    } else { $null }
    $isStale = $null -ne $salesAgeMonths -and $salesAgeMonths -gt 24
    $withinModelBand = $null -ne $modelLow -and $null -ne $modelHigh -and $salesMcv -ge $modelLow -and $salesMcv -le $modelHigh
    $echoRisk = [math]::Abs($model - $salesMcv) -le [math]::Max(5, $salesMcv * 0.05)

    $offices = Get-Number $account.Number_of_Offices__c
    $employees = Get-Number $account.NumberOfEmployees
    $supportSignals = @()
    if ($null -ne $offices -and $offices -ge 3) { $supportSignals += "offices=$offices" }
    if ($null -ne $employees -and $employees -ge 20) { $supportSignals += "employees=$employees" }
    if ($account.Closing_Activity_Evidence__c -eq 'true') { $supportSignals += 'closing-activity-evidence' }

    $classification = if ($echoRisk) {
        'anchor_echo_not_independent'
    } elseif ($ratio -ge 3 -and $absoluteDelta -ge 100) {
        'model_much_higher'
    } elseif ($ratio -ge 2 -and $absoluteDelta -ge 50) {
        'model_higher'
    } elseif ($ratio -le (1.0 / 3.0) -and $absoluteDelta -le -100) {
        'sales_much_higher'
    } elseif ($ratio -le 0.5 -and $absoluteDelta -le -50) {
        'sales_higher'
    } else {
        'directionally_aligned'
    }

    $reviewPriority = if (
        $classification -eq 'model_much_higher' -and
        $account.AI_Prospect_Value_Confidence__c -in @('High', 'Medium') -and
        $supportSignals.Count -gt 0
    ) {
        'P1_possible_sales_underestimate'
    } elseif ($classification -eq 'sales_much_higher' -and -not $isStale) {
        'P1_possible_model_underestimate'
    } elseif ($classification -in @('model_much_higher', 'model_higher', 'sales_much_higher', 'sales_higher')) {
        'P2_review'
    } else {
        'No_immediate_review'
    }

    [pscustomobject]@{
        AccountId = $account.Id
        AccountName = $account.Name
        OwnerName = $account.'Owner.Name'
        AccountType = $account.Type
        CompanyType = $account.Company_Type__c
        State = $account.BillingState
        Website = $account.Website
        CurrentSegment = $account.Account_Segment_v2__c
        ModelMCV = $model
        ModelMCVLow = $modelLow
        ModelMCVHigh = $modelHigh
        ModelARRPoint = Get-Number $account.AI_Prospect_Value_ARR_Point__c
        ModelConfidence = $account.AI_Prospect_Value_Confidence__c
        ModelAction = $account.AI_Prospect_Value_Action__c
        ModelICP = $account.AI_Prospect_Value_ICP__c
        ModelRunId = $account.AI_Prospect_Value_Run_Id__c
        SalesMCV = $salesMcv
        SalesMCVSource = $salesSource
        SalesInputRecordId = $salesRecordId
        SalesInputDate = if ($null -ne $salesDate) { $salesDate.ToString('yyyy-MM-dd') } else { $null }
        SalesInputAgeMonths = $salesAgeMonths
        SalesStage = $salesStage
        SalesInputIsStale = $isStale
        ModelToSalesRatio = [math]::Round($ratio, 2)
        AbsoluteMCVDelta = $absoluteDelta
        SalesMCVWithinModelRange = $withinModelBand
        SalesMCVOutsideModelRange = -not $withinModelBand
        AnchorEchoRisk = $echoRisk
        SupportSignalCount = $supportSignals.Count
        SupportSignals = $supportSignals -join '; '
        VarianceClassification = $classification
        ReviewPriority = $reviewPriority
    }
}

$allPath = Join-Path $InputDirectory 'mcv_sales_model_reconciliation.csv'
$p1Path = Join-Path $InputDirectory 'mcv_p1_review_queue.csv'
$results | Sort-Object ReviewPriority, @{Expression = 'ModelToSalesRatio'; Descending = $true} |
    Export-Csv -LiteralPath $allPath -NoTypeInformation -Encoding utf8
$results | Where-Object { $_.ReviewPriority -like 'P1_*' } |
    Sort-Object ReviewPriority, @{Expression = { [math]::Abs($_.AbsoluteMCVDelta) }; Descending = $true} |
    Export-Csv -LiteralPath $p1Path -NoTypeInformation -Encoding utf8

$summary = [ordered]@{
    generated_at_utc = $now.ToString('o')
    scored_accounts_with_sales_mcv = @($results).Count
    classification_counts = [ordered]@{}
    priority_counts = [ordered]@{}
    source_counts = [ordered]@{}
}
foreach ($group in ($results | Group-Object VarianceClassification | Sort-Object Name)) {
    $summary.classification_counts[$group.Name] = $group.Count
}
foreach ($group in ($results | Group-Object ReviewPriority | Sort-Object Name)) {
    $summary.priority_counts[$group.Name] = $group.Count
}
foreach ($group in ($results | Group-Object SalesMCVSource | Sort-Object Name)) {
    $summary.source_counts[$group.Name] = $group.Count
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $InputDirectory 'mcv_reconciliation_summary.json') -Encoding utf8

Write-Output "Reconciled accounts: $(@($results).Count)"
$results | Group-Object VarianceClassification | Sort-Object Count -Descending | Format-Table Count, Name -AutoSize
$results | Group-Object ReviewPriority | Sort-Object Count -Descending | Format-Table Count, Name -AutoSize
Write-Output "All rows: $allPath"
Write-Output "P1 queue: $p1Path"
