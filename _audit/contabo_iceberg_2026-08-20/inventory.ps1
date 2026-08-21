$ErrorActionPreference = 'Continue'
$dir = "C:\data\iceberg"
$outCsv = "C:\FluxQuantumAI\_audit\contabo_iceberg_2026-08-20\inventory.csv"
$files = Get-ChildItem $dir -Filter "iceberg_*.jsonl" -File -ErrorAction SilentlyContinue

$rows = foreach ($f in $files) {
    $lineCount = 0
    $reader = New-Object System.IO.StreamReader($f.FullName)
    try {
        while ($null -ne $reader.ReadLine()) { $lineCount++ }
    } finally {
        $reader.Close()
    }
    [PSCustomObject]@{
        Name          = $f.Name
        SizeBytes     = $f.Length
        LastWriteTime = $f.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
        LineCount     = $lineCount
    }
}

$rows | Export-Csv -Path $outCsv -NoTypeInformation -Encoding UTF8
Write-Output "DONE: $($rows.Count) files processed"
