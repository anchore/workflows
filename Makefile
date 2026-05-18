.PHONY: *
.DEFAULT_GOAL: make-default

make-default:
	@go run -C .make .

.DEFAULT:
%:
	@go run -C .make . $@
