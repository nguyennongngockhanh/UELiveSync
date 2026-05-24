#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"
#include "Widgets/Text/STextBlock.h"

class UUELiveSyncSubsystem;

class SLiveSyncDiagnosticsWidget :
    public SCompoundWidget
{
public:

    SLATE_BEGIN_ARGS(
        SLiveSyncDiagnosticsWidget) {}
    SLATE_END_ARGS()

    void Construct(
        const FArguments& InArgs);

    virtual void Tick(
        const FGeometry& AllottedGeometry,
        const double InCurrentTime,
        const float InDeltaTime) override;

    virtual bool SupportsKeyboardFocus()
        const override
    {
        return false;
    }

private:

    void RefreshText();

    double LastRefreshTime = 0.0;

    TWeakObjectPtr<
        UUELiveSyncSubsystem>
        WeakSubsystem;

    TSharedPtr<STextBlock> ContentBlock;
};
