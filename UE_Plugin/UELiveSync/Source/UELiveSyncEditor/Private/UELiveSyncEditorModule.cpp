#include "UELiveSyncEditorModule.h"

#include "SLiveSyncStatusWidget.h"

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


void FUELiveSyncEditorModule::
StartupModule()
{
    // Register nomad tab spawner
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
}


void FUELiveSyncEditorModule::
ShutdownModule()
{
    FGlobalTabmanager::Get()
        ->UnregisterNomadTabSpawner(
            LiveSyncStatusTabName);
}


#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(
    FUELiveSyncEditorModule,
    UELiveSyncEditor)
