def call(Closure body) {
    if (this.metaClass.respondsTo(this, 'withFolderProperties', Closure)) {
        withFolderProperties(body)
    } else {
        body()
    }
}
