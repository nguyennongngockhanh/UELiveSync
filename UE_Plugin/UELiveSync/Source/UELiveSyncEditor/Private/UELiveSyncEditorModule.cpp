#include "UELiveSyncEditorModule.h"

#include "SLiveSyncStatusWidget.h"
#include "SLiveSyncDiagnosticsWidget.h"

#include "Editor.h"
#include "LevelEditor.h"

#include "Framework/Docking/TabManager.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "FUELiveSyncEditorModule"

static const FName
    LiveSyncStatusTabName =
    TEXT("LiveSyncStatus");

static const FName
    LiveSyncDiagnosticsTabName =
    TEXT("LiveSyncDiagnostics");


TSharedRef<SDockTab>
CreateLiveSyncStatusTab(
    const FSpawnTabArgs&)
{
    return
        SNew(SDockTab)
        .TabRole(
            ETabRole::NomadTab)
        [
            SNew(SBox)
            .Padding(10.0f)
            [
                SNew(SLiveSyncStatusWidget)
            ]
        ];
}


TSharedRef<SDockTab>
CreateLiveSyncDiagnosticsTab(
    const FSpawnTabArgs&)
{
    return
        SNew(SDockTab)
        .TabRole(
            ETabRole::NomadTab)
        [
            SNew(SBox)
            .Padding(4.0f)
            [
                SNew(SLiveSyncDiagnosticsWidget)
            ]
        ];
}


void FUELiveSyncEditorModule::
StartupModule()
{
    // Register status tab
    FGlobalTabmanager::Get()
        ->RegisterNomadTabSpawner(
            LiveSyncStatusTabName,
            FOnSpawnTab::CreateStatic(
                &CreateLiveSyncStatusTab))
        .SetDisplayName(
            LOCTEXT(
                "LiveSyncStatusTabTitle",
                "Live Sync Status"))
        .SetTooltipText(
            LOCTEXT(
                "LiveSyncStatusTooltip",
                "Open UE Live Sync connection status"))
        .SetMenuType(
            ETabSpawnerMenuType::
                Enabled);

    // Register diagnostics tab
    FGlobalTabmanager::Get()
        ->RegisterNomadTabSpawner(
            LiveSyncDiagnosticsTabName,
            FOnSpawnTab::CreateStatic(
                &CreateLiveSyncDiagnosticsTab))
        .SetDisplayName(
            LOCTEXT(
                "LiveSyncDiagnosticsTabTitle",
                "Live Sync Diagnostics"))
        .SetTooltipText(
            LOCTEXT(
                "LiveSyncDiagnosticsTooltip",
                "Open UE Live Sync runtime diagnostics panel"))
        .SetMenuType(
            ETabSpawnerMenuType::
                Enabled);
}


void FUELiveSyncEditorModule::
ShutdownModule()
{
    FGlobalTabmanager::Get()
        ->UnregisterNomadTabSpawner(
            LiveSyncStatusTabName);

    FGlobalTabmanager::Get()
        ->UnregisterNomadTabSpawner(
            LiveSyncDiagnosticsTabName);
}


#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(
    FUELiveSyncEditorModule,
    UELiveSyncEditor)
